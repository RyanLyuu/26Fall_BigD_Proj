"""Reusable preprocessing tools for the Copernicus Marine analysis pipeline."""

__all__ = ["PreprocessConfig", "preprocess_files", "DetectionConfig", "DetectionResult", "detect_events", "CompoundConfig", "CompoundResult", "detect_compound_events", "write_compound_result", "TrackingConfig", "track_anomalies", "join_historical_recent", "detect_evolution"]

def __getattr__(name):
    if name in {"PreprocessConfig", "preprocess_files"}:
        from .cmems_preprocess import PreprocessConfig, preprocess_files
        return {"PreprocessConfig": PreprocessConfig, "preprocess_files": preprocess_files}[name]
    if name in {"DetectionConfig", "DetectionResult", "detect_events"}:
        from .event_detection import DetectionConfig, DetectionResult, detect_events
        return {"DetectionConfig": DetectionConfig, "DetectionResult": DetectionResult, "detect_events": detect_events}[name]
    if name in {"CompoundConfig", "CompoundResult", "detect_compound_events", "write_compound_result"}:
        from . import compound_detection
        return getattr(compound_detection, name)
    if name in {"TrackingConfig", "track_anomalies"}:
        from . import ocetrac_tracking
        return getattr(ocetrac_tracking, name)
    if name in {"join_historical_recent", "detect_evolution"}:
        from . import evolution
        return getattr(evolution, name)
    raise AttributeError(name)
