"""Dummy gym.Env subclasses."""
import gym
from gym.spaces import Box
import numpy as np
import torch


class MockEnv(gym.Env):  # pylint: disable=abstract-method
    """Dummy environment with continuous action space."""

    def __init__(self, config=None):
        self.config = config or {"action_dim": 4}
        self.horizon = 200
        self.time = 0
        self.observation_space = Box(high=1, low=-1, shape=(4,), dtype=np.float32)
        action_dim = self.config["action_dim"]
        self.action_space = Box(high=1, low=-1, shape=(action_dim,), dtype=np.float32)
        self.goal = np.zeros(self.observation_space.shape, dtype=np.float32)
        self.state = None

    def reset(self):
        self.time = 0
        self.state = self.observation_space.sample()
        return self.state

    def step(self, action):
        self.time += 1
        self.state = np.clip(
            self.state + action, self.observation_space.low, self.observation_space.high
        )
        reward = np.linalg.norm((self.state - self.goal), axis=-1)
        return self.state, reward, self.time >= self.horizon, {}

    def reward_fn(self, state, action, next_state):
        # pylint: disable=missing-docstring,unused-argument
        return torch.norm(next_state - torch.from_numpy(self.goal), dim=-1)
