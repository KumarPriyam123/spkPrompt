"""
Privacy Cloak Tester
--------------------
Tests whether SetWindowDisplayAffinity (WDA_EXCLUDEFROMCAPTURE)
can be applied to any running window.

Requirements:
    pip install pywin32

Usage:
    python privacy_cloak_test.py
"""

import ctypes
import ctypes.wintypes
import sys

try:
    import win32gui
    import win32process
    import win32api
    import win32con
    import psutil
except ImportError:
    print("Missing dependencies. Run: pip install pywin32 psutil")
    sys.exit(1)

WDA_NONE               = 0x00000000
WDA_MONITOR            = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Windows 10 2004+

user32 = ctypes.WinDLL("user32", use_last_error=True)

def set_display_affinity(hwnd, affinity):
    result = user32.SetWindowDisplayAffinity(hwnd, affinity)
    if result == 0:
        err = ctypes.get_last_error()
        return False, err
    return True, None

def get_display_affinity(hwnd):
    affinity = ctypes.wintypes.DWORD(0)
    result = user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity))
    if result == 0:
        return None
    return affinity.value

def get_exe_from_hwnd(hwnd):
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = psutil.Process(pid)
        return proc.name()
    except Exception:
        return "unknown"

def list_visible_windows():
    windows = []

    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title.strip():
                exe = get_exe_from_hwnd(hwnd)
                windows.append((hwnd, title, exe))

    win32gui.EnumWindows(callback, None)
    return windows

def test_window(hwnd, title, exe):
    print(f"\n{'='*55}")
    print(f"  App  : {exe}")
    print(f"  Title: {title[:50]}")
    print(f"  HWND : {hwnd}")

    before = get_display_affinity(hwnd)
    print(f"  Current affinity: {hex(before) if before is not None else 'unreadable'}")

    # Try applying the flag
    ok, err = set_display_affinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
    if ok:
        after = get_display_affinity(hwnd)
        if after == WDA_EXCLUDEFROMCAPTURE:
            print(f"  Result: ✅ SUCCESS — window is now invisible to WGC recorders")
        else:
            print(f"  Result: ⚠️  API accepted but affinity didn't change (app may override)")
        # Restore original
        set_display_affinity(hwnd, before if before is not None else WDA_NONE)
        print(f"  Restored to original affinity.")
    else:
        WIN_ERRORS = {
            5:    "Access Denied (app running at higher privilege or protected process)",
            87:   "Invalid Parameter",
            1400: "Invalid HWND",
        }
        reason = WIN_ERRORS.get(err, f"Unknown error code {err}")
        print(f"  Result: ❌ FAILED — {reason}")

def main():
    print("=" * 55)
    print("   Privacy Cloak Tester — WDA_EXCLUDEFROMCAPTURE")
    print("=" * 55)
    print("\nScanning all visible windows...\n")

    windows = list_visible_windows()

    # Deduplicate by exe name, keeping first window per exe
    seen_exe = {}
    for hwnd, title, exe in windows:
        if exe not in seen_exe:
            seen_exe[exe] = (hwnd, title, exe)

    unique_windows = list(seen_exe.values())
    unique_windows.sort(key=lambda x: x[2].lower())

    print(f"Found {len(unique_windows)} unique apps with visible windows.\n")

    print("Options:")
    print("  [A] Test ALL apps automatically")
    print("  [S] Search and test a specific app by name")
    print("  [L] Just list all detected apps")
    choice = input("\nYour choice: ").strip().upper()

    if choice == "L":
        print("\nDetected apps:")
        for i, (hwnd, title, exe) in enumerate(unique_windows):
            print(f"  {i+1:>3}. {exe:<35} | {title[:40]}")

    elif choice == "A":
        print(f"\nTesting all {len(unique_windows)} apps...\n")
        results = {"success": [], "failed": [], "partial": []}

        for hwnd, title, exe in unique_windows:
            before = get_display_affinity(hwnd)
            ok, err = set_display_affinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            if ok:
                after = get_display_affinity(hwnd)
                if after == WDA_EXCLUDEFROMCAPTURE:
                    results["success"].append(exe)
                else:
                    results["partial"].append(exe)
                set_display_affinity(hwnd, before if before is not None else WDA_NONE)
            else:
                results["failed"].append((exe, err))

        print("\n" + "="*55)
        print("  SUMMARY")
        print("="*55)

        print(f"\n✅ CLOAKABLE ({len(results['success'])} apps):")
        for exe in results["success"]:
            print(f"     {exe}")

        print(f"\n⚠️  PARTIAL ({len(results['partial'])} apps — API accepts but app overrides):")
        for exe in results["partial"]:
            print(f"     {exe}")

        print(f"\n❌ BLOCKED ({len(results['failed'])} apps):")
        WIN_ERRORS = {5: "Access Denied", 87: "Invalid Param", 1400: "Invalid HWND"}
        for exe, err in results["failed"]:
            reason = WIN_ERRORS.get(err, f"Error {err}")
            print(f"     {exe:<35} ({reason})")

    elif choice == "S":
        query = input("Enter app name or keyword: ").strip().lower()
        matches = [(h, t, e) for h, t, e in unique_windows if query in e.lower() or query in t.lower()]
        if not matches:
            print("No matching windows found.")
        else:
            for item in matches:
                test_window(*item)

    print("\nDone.")

if __name__ == "__main__":
    main()
