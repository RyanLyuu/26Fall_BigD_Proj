"""Reusable preprocessing tools for the Copernicus Marine analysis pipeline."""

__all__ = ["PreprocessConfig", "preprocess_files", "DetectionConfig", "DetectionResult", "detect_events"]

def __getattr__(name):
    if name in {"PreprocessConfig", "preprocess_files"}:
        from .cmems_preprocess import PreprocessConfig, preprocess_files
        return {"PreprocessConfig": PreprocessConfig, "preprocess_files": preprocess_files}[name]
    if name in {"DetectionConfig", "DetectionResult", "detect_events"}:
        from .event_detection import DetectionConfig, DetectionResult, detect_events
        return {"DetectionConfig": DetectionConfig, "DetectionResult": DetectionResult, "detect_events": detect_events}[name]
    raise AttributeError(name)
