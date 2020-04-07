"""PyTorch related utilities."""
import functools
import inspect

import numpy as np
import torch
import torch.nn as nn
from torch.autograd import grad


def flat_grad(outputs, inputs, *args, **kwargs):
    """Compute gradients and return a flattened array."""
    params = list(inputs)
    grads = grad(outputs, params, *args, **kwargs)
    zeros = torch.zeros
    return torch.cat(
        [zeros(p.numel()) if g is None else g.flatten() for p, g in zip(params, grads)]
    )


def convert_to_tensor(arr, device):
    """Convert array-like object to tensor and cast it to appropriate device.

    Arguments:
        arr (object): array-like object which can be converted using `np.asarray`
        device (torch.device): device to cast the resulting tensor to

    Returns:
        The array converted to a `torch.Tensor`.
    """
    if torch.is_tensor(arr):
        return arr.to(device)
    tensor = torch.from_numpy(np.asarray(arr))
    if tensor.dtype == torch.double:
        tensor = tensor.float()
    return tensor.to(device)


def get_optimizer_class(name):
    """Return the optimizer class given its name.

    Arguments:
        name (str): the optimizer's name

    Returns:
        The corresponding `torch.optim.Optimizer` subclass
    """
    if name in dir(torch.optim):
        cls = getattr(torch.optim, name)
        if issubclass(cls, torch.optim.Optimizer) and cls is not torch.optim.Optimizer:
            return cls
    raise ValueError(f"Couldn't find optimizer with name '{name}'")


def get_activation(activation):
    """Return activation module type from string.

    Arguments:
        activation (str, dict or None): the activation function's description
    """
    if activation is None:
        return None

    if isinstance(activation, dict):
        name = activation["name"]
        options = activation.get("options", {})
    else:
        name = activation
        options = {}

    if name in dir(nn.modules.activation):
        cls = getattr(nn.modules.activation, name)
        if issubclass(cls, nn.Module):
            return functools.partial(cls, **options)
    raise ValueError(f"Couldn't find activation with name '{name}'")


def get_initializer(name):
    """Return initializer function given its name.

    Arguments:
        name (str): the initializer function's name
    """
    name_ = name + "_"
    if name in dir(nn.init) and name_ in dir(nn.init):
        func = getattr(nn.init, name_)
        return func
    raise ValueError(f"Couldn't find initializer with name '{name}'")


def update_polyak(from_module, to_module, polyak):
    """Update parameters between modules by polyak averaging.

    Arguments:
        from_module (nn.Module): Module whose parameters are targets.
        to_module (nn.Module): Module whose parameters are updated towards the targets.
        polyak (float): Averaging factor. The higher it is, the slower the parameters
            are updated.
    """
    for source, target in zip(from_module.parameters(), to_module.parameters()):
        target.data.mul_(polyak).add_(1 - polyak, source.data)


NONLINEARITY_MAP = {
    "Sigmoid": "sigmoid",
    "Tanh": "tanh",
    "ReLU": "relu",
    "ELU": "relu",
    "LeakyReLU": "leaky_relu",
}


def initialize_(name, activation=None, **options):
    """Return a callable to apply an initializer with the given name and options.

    If `gain` is part of the initializer's argspec and is not specified in options,
    the recommended value from `nn.init.calculate_gain` is used.

    Arguments:
        name (str): name of initializer function
        activation (str, dict): activation function following linear layer, optional
        **options: keyword arguments to be passed to the initializer

    Returns:
        A callable to be used with `nn.Module.apply`.
    """

    initializer = get_initializer(name)

    if isinstance(activation, dict):
        activation = activation["name"]
        options.update(activation.get("options", {}))

    if (
        activation in NONLINEARITY_MAP
        and "gain" not in options
        and "gain" in inspect.signature(initializer).parameters
    ):
        recommended_gain = nn.init.calculate_gain(
            NONLINEARITY_MAP[activation], param=options.get("negative_slope")
        )
        options["gain"] = recommended_gain
    func_ = functools.partial(initializer, **options)

    def init(module):
        if isinstance(module, nn.Linear):
            func_(module.weight)
            if module.bias is not None:
                nn.init.constant_(module.bias, 0)

    return init


def perturb_module_params(module, target_module, stddev):
    """Load state dict from another module and perturb parameters not in layer norms.

    Arguments:
        module (nn.Module): the module to perturb
        target_module (nn.Module): the module to copy from
        stddev (float): the gaussian standard deviation with which to perturb parameters
            excluding those from layer norms
    """
    module.load_state_dict(target_module.state_dict())

    layer_norms = (m for m in module.modules() if isinstance(m, nn.LayerNorm))
    layer_norm_params = set(p for m in layer_norms for p in m.parameters())
    to_perturb = (p for p in module.parameters() if p not in layer_norm_params)

    for param in to_perturb:
        param.data.add_(torch.randn_like(param) * stddev)


def trace(func):
    """
    Wrapps and automatically traces an instance function on first call.

    Arguments:
        func (callable): the callable to be converted to TorchScript. Should not
            have any input-dependent control flow.
    """
    method_name = "_traced_" + func.__name__

    @functools.wraps(func)
    def wrapped(self, *args):
        if not hasattr(self, method_name):
            traced = torch.jit.trace(functools.partial(func, self), args)
            setattr(self, method_name, traced)
        return getattr(self, method_name)(*args)

    return wrapped
