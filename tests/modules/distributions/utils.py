# pylint:disable=missing-docstring


def _test_dist_ops(dist, params, batch_shape, event_shape, sample_shape):
    # pylint:disable=too-many-arguments
    sample = dist.sample(params, sample_shape)
    assert sample.shape == sample_shape + batch_shape + event_shape
    rsample = dist.rsample(params, sample_shape)
    if rsample is not None:
        assert rsample.shape == sample_shape + batch_shape + event_shape

    log_prob = dist.log_prob(params, sample)
    assert log_prob.shape == sample_shape + batch_shape
    entropy = dist.entropy(params)
    assert entropy.shape == batch_shape
    perplexity = dist.perplexity(params)
    assert perplexity.shape == entropy.shape

    cdf = dist.cdf(params, sample)
    if cdf is not None:
        assert cdf.shape == sample_shape + batch_shape
        icdf = dist.icdf(params, cdf)
        if icdf is not None:
            assert icdf.shape == sample.shape
