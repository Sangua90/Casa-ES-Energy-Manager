from custom_components.casa_es_energy_manager.pv_estimator_v141 import (
    AdaptivePVPotentialEstimator,
)


def test_measured_only_fallback_is_exact():
    estimator = AdaptivePVPotentialEstimator()
    data = estimator.update(
        measured_pv_w=2200,
        load_w=1800,
        grid_power_w=0,
        battery_power_w=400,
        custom_forecast_w=None,
        provider_forecast_w=None,
        inverter_limit_w=10000,
    )
    assert data["pv_potential_w"] == 2200
    assert data["pv_potential_gap_w"] == 0
    assert data["pv_estimator_source"] == "measured_only"
    assert data["pv_estimator_confidence"] == 100.0


def test_custom_forecast_has_stronger_initial_weight():
    estimator = AdaptivePVPotentialEstimator()
    data = estimator.update(
        measured_pv_w=1800,
        load_w=1800,
        grid_power_w=0,
        battery_power_w=0,
        custom_forecast_w=5200,
        provider_forecast_w=7000,
        inverter_limit_w=10000,
    )
    assert data["pv_estimator_source"] == "blended"
    # The site-calibrated custom forecast starts with the better prior, so the
    # fused estimate must stay closer to 5.2 kW than to the provider's 7 kW.
    assert data["pv_potential_raw_fused_w"] < 6100
    assert data["pv_potential_w"] >= 1800


def test_estimator_never_goes_below_measured_pv():
    estimator = AdaptivePVPotentialEstimator()
    data = estimator.update(
        measured_pv_w=4800,
        load_w=5000,
        grid_power_w=200,
        battery_power_w=0,
        custom_forecast_w=3500,
        provider_forecast_w=3900,
        inverter_limit_w=10000,
    )
    assert data["pv_potential_w"] >= 4800
    assert data["pv_potential_gap_w"] == 0


def test_clear_grid_import_creates_learning_samples():
    estimator = AdaptivePVPotentialEstimator()
    for _ in range(30):
        data = estimator.update(
            measured_pv_w=4500,
            load_w=6000,
            grid_power_w=1500,
            battery_power_w=0,
            custom_forecast_w=4700,
            provider_forecast_w=6000,
            inverter_limit_w=10000,
        )

    assert data["pv_estimator_learning_sample"] is True
    assert data["pv_estimator_custom_samples"] == 30
    assert data["pv_estimator_provider_samples"] == 30
    # Repeated real observations should teach the provider a stronger downward
    # correction than the already-close custom virtual forecast.
    assert data["pv_estimator_provider_factor"] < data["pv_estimator_custom_factor"]
    assert data["pv_estimator_custom_error_pct"] < data["pv_estimator_provider_error_pct"]


def test_zero_export_clipping_does_not_train_forecasts_down():
    estimator = AdaptivePVPotentialEstimator()
    before_custom = estimator.custom.factor
    before_provider = estimator.provider.factor
    data = estimator.update(
        measured_pv_w=1500,
        load_w=1500,
        grid_power_w=0,
        battery_power_w=0,
        custom_forecast_w=5200,
        provider_forecast_w=5600,
        inverter_limit_w=10000,
    )
    assert data["pv_estimator_learning_sample"] is False
    assert estimator.custom.factor == before_custom
    assert estimator.provider.factor == before_provider
    assert data["pv_potential_w"] > 1500


def test_inverter_limit_caps_potential():
    estimator = AdaptivePVPotentialEstimator()
    data = estimator.update(
        measured_pv_w=5000,
        load_w=5000,
        grid_power_w=0,
        battery_power_w=0,
        custom_forecast_w=13000,
        provider_forecast_w=12000,
        inverter_limit_w=10000,
    )
    assert data["pv_potential_raw_fused_w"] == 10000
    assert data["pv_potential_w"] <= 10000
