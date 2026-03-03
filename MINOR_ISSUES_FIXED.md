# Minor Issues Fixed

**Date:** 2026-03-04
**Status:** ✅ RESOLVED

---

## Issues Fixed

### 1. ✅ Config Path Resolution

**Problem:**
The default config path was looking in `src/config/tickers.yaml` instead of the project root `config/tickers.yaml`.

**Root Cause:**
```python
# Before (line 35-36 in loader.py)
DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "tickers.yaml"
)
```
- `__file__` = `src/equity_lake/config/loader.py`
- `.parents[2]` only goes up to `src/` level

**Fix:**
```python
# After (line 35-36 in loader.py)
DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "tickers.yaml"
)
```
- `.parents[3]` now correctly goes to project root

**Verification:**
```bash
$ uv run equity-daily --list-stats
Loaded ticker config from /Users/minghao/Desktop/personal/equity_lake/config/tickers.yaml: 97 tickers across 4 markets
```

✅ Config now loads from project root by default

---

### 2. ✅ Datetime Deprecation Warning

**Problem:**
```python
datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version.
Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
```

**Root Cause:**
```python
# Before (line 70 in logging.py)
event_dict["timestamp"] = datetime.utcnow().isoformat() + "Z"
```

**Fix:**
```python
# After (line 70 in logging.py)
from datetime import datetime, timezone

event_dict["timestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
```

**Changes:**
1. Import `timezone` from `datetime` module
2. Use `datetime.now(timezone.utc)` instead of `datetime.utcnow()`
3. Use `.replace("+00:00", "Z")` to maintain "Z" suffix format

**Verification:**
```bash
$ uv run equity-daily --list-stats 2>&1 | grep -i "deprecation"
# No output = no deprecation warnings ✅
```

✅ No more deprecation warnings

---

## Files Modified

1. **src/equity_lake/config/loader.py**
   - Line 36: Changed `.parents[2]` to `.parents[3]`

2. **src/equity_lake/core/logging.py**
   - Line 14: Added `timezone` import
   - Line 70: Replaced `datetime.utcnow()` with `datetime.now(timezone.utc)`

---

## Testing

Both fixes have been verified:

```bash
# Test 1: Config path
$ uv run equity-daily --list-stats
✅ Config loads from project root: /path/to/equity_lake/config/tickers.yaml

# Test 2: No deprecation warnings
$ uv run equity-monitor --max-age-days 1
✅ Clean output with no deprecation warnings
```

---

## Impact

- ✅ **Breaking Changes:** None
- ✅ **Backward Compatibility:** Fully maintained
- ✅ **Documentation:** No updates needed (already correct)
- ✅ **User Experience:** Improved (more intuitive config path)

---

**Status:** Both issues are now fully resolved. ✅
