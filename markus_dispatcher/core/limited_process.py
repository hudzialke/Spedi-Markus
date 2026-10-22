"""Windows child process with a kernel-enforced committed-memory ceiling.

The child starts suspended, joins the Job Object, then resumes. A failure to
establish a limit kills the child; there is deliberately no unbounded fallback.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import subprocess

OCR_MEMORY_BYTES = 2 * 1024**3


class ResourceLimitError(RuntimeError):
    pass


class _BasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimits), ('IoInfo', _IoCounters),
                ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                ('th32ThreadID', wintypes.DWORD), ('th32OwnerProcessID', wintypes.DWORD),
                ('tpBasePri', wintypes.LONG), ('tpDeltaPri', wintypes.LONG), ('dwFlags', wintypes.DWORD)]


class WindowsMemoryJob:
    def __init__(self, memory_bytes: int):
        if os.name != 'nt':
            raise ResourceLimitError('Limit pamięci OCR wymaga obsługiwanej wersji Windows.')
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        definitions = {
            'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            'SetInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            'QueryInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            'CreateToolhelp32Snapshot': ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            'Thread32First': ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            'Thread32Next': ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            'OpenThread': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'ResumeThread': ([wintypes.HANDLE], wintypes.DWORD),
            'TerminateJobObject': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (args, result) in definitions.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ResourceLimitError('Windows nie pozwolił utworzyć limitu pamięci OCR.')
        limits = _ExtendedLimits()
        # PROCESS_MEMORY | JOB_MEMORY | KILL_ON_JOB_CLOSE. Shared memory of all
        # children is bounded by the aggregate job limit; threads share a process.
        limits.BasicLimitInformation.LimitFlags = 0x100 | 0x200 | 0x2000
        limits.ProcessMemoryLimit = limits.JobMemoryLimit = memory_bytes
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ResourceLimitError('Windows nie pozwolił ustawić limitu pamięci OCR. Odczyt nie został uruchomiony.')

    def assign_and_resume(self, process: subprocess.Popen):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ResourceLimitError('Nie udało się ograniczyć pamięci procesu OCR. Odczyt zatrzymano.')
        snapshot = self.kernel.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ResourceLimitError('Nie można bezpiecznie uruchomić ograniczonego procesu OCR.')
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            available = self.kernel.Thread32First(snapshot, ctypes.byref(entry))
            while available:
                if entry.th32OwnerProcessID == process.pid:
                    thread = self.kernel.OpenThread(0x2, False, entry.th32ThreadID)  # THREAD_SUSPEND_RESUME
                    if not thread:
                        break
                    try:
                        if self.kernel.ResumeThread(thread) == 0xffffffff:
                            break
                        return
                    finally:
                        self.kernel.CloseHandle(thread)
                available = self.kernel.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            self.kernel.CloseHandle(snapshot)
        raise ResourceLimitError('Nie można wznowić ograniczonego procesu OCR.')

    def peak_memory(self) -> int:
        information = _ExtendedLimits()
        if self.kernel.QueryInformationJobObject(self.handle, 9, ctypes.byref(information), ctypes.sizeof(information), None):
            return information.PeakJobMemoryUsed
        return 0

    def terminate(self):
        if self.handle:
            self.kernel.TerminateJobObject(self.handle, 1)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def run_limited(args, *, input: bytes | None = None, env=None, timeout: float = 45,
                memory_bytes: int = OCR_MEMORY_BYTES):
    if memory_bytes <= 0:
        raise ValueError('Limit pamięci musi być dodatni.')
    job = WindowsMemoryJob(memory_bytes)
    process = None
    try:
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=env, creationflags=subprocess.CREATE_NO_WINDOW | 0x4)  # CREATE_SUSPENDED
        job.assign_and_resume(process)
        try:
            stdout, stderr = process.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired:
            job.terminate()
            process.communicate()
            # No input, command, path or partial OCR content in the exception.
            raise subprocess.TimeoutExpired('local OCR', timeout) from None
        result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        result.peak_memory_bytes = job.peak_memory()
        return result
    except BaseException:
        if process is not None and process.poll() is None:
            job.terminate()
            process.kill()  # Also works if assigning the suspended process failed.
            process.communicate()
        raise
    finally:
        if process is not None:
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
        job.close()
