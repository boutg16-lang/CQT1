import inspect
import ntpath
import sys


def patch_speechbrain_inspect_compat():
    """Keep SpeechBrain lazy imports from breaking inspect on Windows."""
    importutils = sys.modules.get("speechbrain.utils.importutils")
    lazy_module_class = getattr(importutils, "LazyModule", None)
    if not isinstance(lazy_module_class, type):
        return False

    original = getattr(lazy_module_class, "ensure_module", None)
    if not callable(original):
        return False
    if getattr(original, "_cqt_inspect_compat", False):
        return True

    inspect_path = ntpath.normcase(ntpath.normpath(inspect.__file__))

    def ensure_module(self, stacklevel):
        try:
            caller_path = sys._getframe(stacklevel + 1).f_code.co_filename
        except ValueError:
            return original(self, stacklevel + 1)
        if ntpath.normcase(ntpath.normpath(caller_path)) == inspect_path:
            raise AttributeError()
        return original(self, stacklevel + 1)

    ensure_module._cqt_inspect_compat = True
    lazy_module_class.ensure_module = ensure_module
    return True
