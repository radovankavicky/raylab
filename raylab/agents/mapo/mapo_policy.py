"""Policy for MAPO using PyTorch."""
import collections

import torch
import torch.nn as nn
from ray.rllib import SampleBatch
from ray.rllib.utils.annotations import override

import raylab.policy as raypi
import raylab.utils.pytorch as ptu
from raylab.envs.rewards import get_reward_fn
from raylab.losses import ClippedDoubleQLearning
from raylab.losses import ModelAwareDPG
from raylab.losses.utils import clipped_action_value


class MAPOTorchPolicy(raypi.TargetNetworksMixin, raypi.TorchPolicy):
    """Model-Aware Policy Optimization policy in PyTorch to use with RLlib."""

    # pylint: disable=abstract-method

    def __init__(self, observation_space, action_space, config):
        assert (
            config.get("module", {}).get("torch_script", False) is False
        ), "MAPO uses operations incompatible with TorchScript."
        super().__init__(observation_space, action_space, config)

        self.reward_fn = get_reward_fn(self.config["env"], self.config["env_config"])
        self.transition = None

        self.loss_actor = None
        if not self.config["true_model"]:
            self.check_model(
                self.module.model.rsample
                if self.config["grad_estimator"] == "PD"
                else self.module.model.sample
            )
            self.module.model.zero_grad()
        self.loss_critic = ClippedDoubleQLearning(
            self.module.critics,
            self.module.target_critics,
            self.module.target_actor,
            gamma=self.config["gamma"],
        )

    @staticmethod
    @override(raypi.TorchPolicy)
    def get_default_config():
        """Return the default configuration for MAPO."""
        # pylint: disable=cyclic-import
        from raylab.agents.mapo.mapo import DEFAULT_CONFIG

        return DEFAULT_CONFIG

    @override(raypi.TorchPolicy)
    def make_module(self, obs_space, action_space, config):
        module_config = config["module"]
        module_config.setdefault("critic", {})
        module_config["critic"]["double_q"] = config["clipped_double_q"]
        module_config.setdefault("actor", {})
        module_config["actor"]["perturbed_policy"] = (
            config["exploration_config"]["type"]
            == "raylab.utils.exploration.ParameterNoise"
        )
        # pylint:disable=no-member
        return super().make_module(obs_space, action_space, config)

    @override(raypi.TorchPolicy)
    def make_optimizer(self):
        config = self.config["torch_optimizer"]
        components = "model actor critics".split()
        if self.config["true_model"]:
            components = components[1:]

        optims = {k: ptu.build_optimizer(self.module[k], config[k]) for k in components}
        return collections.namedtuple("OptimizerCollection", components)(**optims)

    def set_transition_kernel(self, transition_kernel):
        """Use an external transition kernel to sample imaginary states."""
        torch_script = self.config["module"]["torch_script"]
        transition = EnvTransition(
            self.observation_space,
            self.action_space,
            transition_kernel,
            torch_script=torch_script,
        )
        self.transition = torch.jit.script(transition) if torch_script else transition
        self.check_model(self.transition)

    def check_model(self, model):
        """Verify that the transition model is appropriate for the desired estimator."""
        obs = torch.randn(self.observation_space.shape)[None]
        act = torch.randn(self.action_space.shape)[None]
        if self.config["grad_estimator"] == "SF":
            sample, logp = model(obs, act.requires_grad_())
            assert sample.grad_fn is None
            assert logp is not None
            logp.mean().backward()
            assert (
                act.grad is not None
            ), "Transition grad log_prob must exist for SF estimator"
            assert not torch.allclose(act.grad, torch.zeros_like(act))
        if self.config["grad_estimator"] == "PD":
            sample, _ = model(obs.requires_grad_(), act.requires_grad_())
            sample.mean().backward()
            assert (
                act.grad is not None
            ), "Transition grad w.r.t. state and action must exist for PD estimator"
            assert not torch.allclose(act.grad, torch.zeros_like(act))

        self.loss_actor = ModelAwareDPG(
            model,
            self.module.actor,
            self.module.critics,
            self.reward_fn,
            gamma=self.config["gamma"],
            num_model_samples=self.config["num_model_samples"],
            grad_estimator=self.config["grad_estimator"],
        )

    @override(raypi.TorchPolicy)
    def learn_on_batch(self, samples):
        batch_tensors = self._lazy_tensor_dict(samples)

        info = {}
        info.update(self._update_critic(batch_tensors))
        if not self.config["true_model"]:
            info.update(self._update_model(batch_tensors))
        info.update(self._update_actor(batch_tensors))

        self.update_targets("critics", "target_critics")
        return self._learner_stats(info)

    def learn_critic(self, samples):
        """Update critics with samples."""
        batch_tensors = self._lazy_tensor_dict(samples)
        info = {}
        info.update(self._update_critic(batch_tensors))
        self.update_targets("critics", "target_critics")
        return self._learner_stats(info)

    def learn_model(self, samples):
        """Update model with samples."""
        batch_tensors = self._lazy_tensor_dict(samples)
        info = {}
        info.update(self._update_model(batch_tensors))
        return self._learner_stats(info)

    def learn_actor(self, samples):
        """Update actor with samples."""
        batch_tensors = self._lazy_tensor_dict(samples)
        info = {}
        info.update(self._update_actor(batch_tensors))
        return self._learner_stats(info)

    def _update_critic(self, batch_tensors):
        with self.optimizer.critics.optimize():
            critic_loss, info = self.loss_critic(batch_tensors)
            critic_loss.backward()

        info.update(self.extra_grad_info("critics"))
        return info

    def _update_model(self, batch_tensors):
        with self.optimizer.model.optimize():
            mle_loss, info = self.mle_loss(batch_tensors)

            if self.config["model_loss"] == "DAML":
                daml_loss, daml_info = self.daml_loss(batch_tensors)
                info.update(daml_info)

                alpha = self.config["mle_interpolation"]
                model_loss = alpha * mle_loss + (1 - alpha) * daml_loss
            else:
                model_loss = mle_loss

            model_loss.backward()

        info.update(self.extra_grad_info("model"))
        return info

    def daml_loss(self, batch_tensors):
        """Compute policy gradient-aware (PGA) model loss."""
        obs = batch_tensors[SampleBatch.CUR_OBS]
        actions = self.module.actor(obs).detach().requires_grad_()

        predictions = self.one_step_action_value_surrogate(obs, actions)
        targets = self.zero_step_action_values(obs, actions)

        temporal_diff = torch.sum(targets - predictions)
        (action_gradients,) = torch.autograd.grad(
            temporal_diff, actions, create_graph=True
        )

        daml_loss = torch.sum(action_gradients * action_gradients, dim=-1).mean()
        return (
            daml_loss,
            {"loss(action)": temporal_diff.item(), "loss(daml)": daml_loss.item()},
        )

    def one_step_action_value_surrogate(self, obs, actions, model_samples=1):
        """
        Compute 1-step approximation of Q^{\\pi}(s, a) for Deterministic Policy Gradient
        using target networks and model transitions.
        """
        actor = self.module.actor
        critics = self.module.critics
        sampler = (
            self.transition
            if self.config["true_model"]
            else self.module.model.sample
            if self.config["grad_estimator"] == "SF"
            else self.module.model.rsample
        )

        next_obs, rewards, logp = self._generate_transition(
            obs, actions, self.reward_fn, sampler, model_samples
        )
        # Next action grads shouldn't propagate
        with torch.no_grad():
            next_acts = actor(next_obs)
        next_values = clipped_action_value(next_obs, next_acts, critics)
        values = rewards + self.config["gamma"] * next_values

        if self.config["grad_estimator"] == "SF":
            surrogate = torch.mean(logp * values.detach(), dim=0)
        elif self.config["grad_estimator"] == "PD":
            surrogate = torch.mean(values, dim=0)
        return surrogate

    @staticmethod
    def _generate_transition(obs, actions, reward_fn, sampler, num_samples):
        """Compute virtual transition and its log density."""
        sample_shape = (num_samples,)
        obs = obs.expand(sample_shape + obs.shape)
        actions = actions.expand(sample_shape + actions.shape)

        next_obs, logp = sampler(obs, actions)
        rewards = reward_fn(obs, actions, next_obs)
        return next_obs, rewards, logp

    def zero_step_action_values(self, obs, actions):
        """Compute Q^{\\pi}(s, a) directly using approximate critic."""
        return clipped_action_value(obs, actions, self.module.critics)

    def mle_loss(self, batch_tensors):
        """Compute Maximum Likelihood Estimation (MLE) model loss."""
        avg_logp = self.module.model.log_prob(
            batch_tensors[SampleBatch.CUR_OBS],
            batch_tensors[SampleBatch.ACTIONS],
            batch_tensors[SampleBatch.NEXT_OBS],
        ).mean()
        loss = avg_logp.neg()
        return loss, {"loss(mle)": loss.item()}

    def _update_actor(self, batch_tensors):
        with self.optimizer.actor.optimize():
            policy_loss, info = self.loss_actor(batch_tensors)
            policy_loss.backward()

        info.update(self.extra_grad_info("actor"))
        return info

    @torch.no_grad()
    def extra_grad_info(self, component):
        """Clip grad norm and return statistics for component."""
        return {
            f"grad_norm({component})": nn.utils.clip_grad_norm_(
                self.module[component].parameters(),
                self.config["max_grad_norm"][component],
            ).item()
        }


class EnvTransition(nn.Module):
    """Wrapper module around existing env transition function."""

    def __init__(self, obs_space, action_space, transition_kernel, torch_script=False):
        super().__init__()
        if torch_script:
            obs = torch.as_tensor(obs_space.sample())[None]
            action = torch.as_tensor(action_space.sample())[None]
            transition_kernel = torch.jit.trace(transition_kernel, (obs, action))
        self.transition_kernel = transition_kernel

    @override(nn.Module)
    def forward(self, obs, action):  # pylint:disable=arguments-differ
        return self.transition_kernel(obs, action)
