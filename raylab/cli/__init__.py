"""CLI utilities for RayLab."""
import click

from .tune_experiment import experiment
from .best_checkpoint import find_best
from .evaluate_checkpoint import rollout
from .viskit import plot, plot_export


@click.group()
def cli():
    """RayLab: Reinforcement learning algorithms in RLlib."""


@cli.command()
@click.argument(
    "paths",
    nargs=-1,
    type=click.Path(exists=True, file_okay=False, dir_okay=True, resolve_path=False),
)
def dashboard(paths):
    """Launch the experiment dashboard to monitor training progress."""
    from streamlit.cli import _main_run
    from . import experiment_dashboard

    _main_run(experiment_dashboard.__file__, paths)


@cli.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, resolve_path=True),
)
def episodes(path):
    """Launch the episode dashboard to monitor state and action distributions."""
    from streamlit.cli import _main_run
    from . import episode_dashboard

    _main_run(episode_dashboard.__file__, [path])


@cli.command()
@click.argument("agent_id", type=str)
@click.argument(
    "checkpoint",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, resolve_path=True),
)
def test_module(agent_id, checkpoint):
    """Launch dashboard to test generative models from a checkpoint."""
    from streamlit.cli import _main_run
    from . import test_stochastic_module

    _main_run(test_stochastic_module.__file__, [agent_id, checkpoint])


cli.add_command(experiment)
cli.add_command(find_best)
cli.add_command(rollout)
cli.add_command(plot)
cli.add_command(plot_export)
