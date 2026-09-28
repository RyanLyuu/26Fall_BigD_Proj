"""Reusable preprocessing tools for the Copernicus Marine analysis pipeline."""

__all__ = ["PreprocessConfig", "preprocess_files"]

def __getattr__(name):
    if name in __all__:
        from .cmems_preprocess import PreprocessConfig, preprocess_files
        return {"PreprocessConfig": PreprocessConfig, "preprocess_files": preprocess_files}[name]
    raise AttributeError(name)
