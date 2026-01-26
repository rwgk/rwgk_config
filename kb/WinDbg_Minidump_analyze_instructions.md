# WinDbg Minidump Analysis Instructions

## Locating Minidump Files

Minidump files are stored in:
```
C:\Windows\Minidump\
```

## Opening WinDbg

1. Search for "WinDbg" or "WinDbg Preview" in the Start menu
2. Run as Administrator (recommended)

## Loading a Dump File

1. `File > Open dump file` (or Ctrl+D)
2. Navigate to `C:\Windows\Minidump\`
3. Select the `.dmp` file to analyze

**Note:** If "Open Crash Dump" is grayed out, close WinDbg entirely and reopen it. This happens when a dump is already loaded.

## Setting Up Symbols (One-Time Setup)

In the command window at the bottom, enter:
```
.sympath srv*C:\Symbols*https://msdl.microsoft.com/download/symbols
```

Then reload symbols:
```
.reload
```

This downloads debug symbols from Microsoft's symbol server and caches them in `C:\Symbols\`.

## Running the Analysis

```
!analyze -v
```

This may take 1-2 minutes the first time as symbols are downloaded. Subsequent runs are faster due to caching.

## Saving Output to a File

To log the analysis to a file:
```
.logopen C:\Users\rgrossekunst\wrk\windbg_analysis.txt
!analyze -v
.logclose
```

## Key Fields in the Output

| Field | Description |
|-------|-------------|
| `BUGCHECK_STR` | Type of crash |
| `IMAGE_NAME` | Driver/module that crashed (e.g., `nvlddmkm.sys`, `dxgmms2.sys`) |
| `MODULE_NAME` | Same as above |
| `SYMBOL_NAME` | Specific function that crashed |
| `FAILURE_BUCKET_ID` | Summary string for the crash cause |
| `STACK_TEXT` | Call stack at crash time |
| `EXCEPTION_CODE` | Error code (e.g., `0xc0000005` = access violation) |

## Common Crash Modules

| Module | Description |
|--------|-------------|
| `nvlddmkm.sys` | NVIDIA kernel-mode driver |
| `dxgmms2.sys` | Windows DirectX Graphics Memory Management |
| `ntoskrnl.exe` | Windows kernel |
| `win32kfull.sys` | Windows graphics subsystem |

## Analyzing Multiple Dumps

To analyze another dump file:
1. Close WinDbg completely
2. Reopen WinDbg
3. Load the next dump file

## Tips

- Analyze the **most recent** dump first - it often has the clearest trigger
- Compare multiple dumps to see if they share the same `FAILURE_BUCKET_ID`
- Search Google for the `FAILURE_BUCKET_ID` string to find known issues
