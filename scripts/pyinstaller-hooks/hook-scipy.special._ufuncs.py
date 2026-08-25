import importlib.util

# PyInstaller's stock hook assumes scipy.special._cdflib exists for every
# SciPy >=1.13 build. SciPy 1.18 on Windows no longer ships that module.
hiddenimports = ["scipy.special._ufuncs_cxx"]
for module in ("scipy.special._cdflib", "scipy.special._special_ufuncs"):
    if importlib.util.find_spec(module) is not None:
        hiddenimports.append(module)
