"""Trusted bootstrap inside Bubblewrap; enforce limits before importing package code."""

import ctypes
import errno
import json
import os
import resource
import runpy
import sys

# No socket, clone/fork/exec, namespace, mount, ptrace, ioctl, keyring, BPF or
# io_uring calls. Default deny makes additions require a reviewed policy change.
SYSCALLS = """
read write readv writev pread64 pwrite64 close close_range lseek fstat stat lstat
newfstatat statx access faccessat faccessat2 open openat getdents getdents64
readlink readlinkat mkdir mkdirat rmdir unlink unlinkat rename renameat renameat2
chmod fchmod fchmodat umask utime utimes futimesat utimensat truncate ftruncate
fsync fdatasync flock fcntl dup dup2 dup3 pipe pipe2
mmap mprotect munmap mremap madvise brk
rt_sigaction rt_sigprocmask rt_sigreturn sigaltstack
getpid getppid gettid getuid geteuid getgid getegid getgroups
getcwd chdir fchdir getrandom clock_gettime clock_getres gettimeofday time
nanosleep clock_nanosleep poll ppoll select pselect6
epoll_create epoll_create1 epoll_ctl epoll_wait epoll_pwait
futex futex_waitv sched_getaffinity sched_yield getrusage sysinfo uname
getrlimit setrlimit prlimit64 restart_syscall exit exit_group
""".split()


def restrict():
    for kind, value in (
        (resource.RLIMIT_AS, 512 * 1024 * 1024),
        (resource.RLIMIT_CPU, 10),
        (resource.RLIMIT_FSIZE, 8 * 1024 * 1024),
        (resource.RLIMIT_NOFILE, 64),
        (resource.RLIMIT_NPROC, 0),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(kind, (value, value))
    library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    policy = library.seccomp_init(0x00050000 | errno.EPERM)
    if not policy:
        raise RuntimeError("seccomp_initialization_failed")
    try:
        for name in SYSCALLS:
            number = library.seccomp_syscall_resolve_name(name.encode("ascii"))
            if (
                number >= 0
                and library.seccomp_rule_add(policy, 0x7FFF0000, number, 0) != 0
            ):
                raise RuntimeError("seccomp_rule_failed")
        if library.seccomp_load(policy) != 0:
            raise RuntimeError("seccomp_load_failed")
    finally:
        library.seccomp_release(policy)


def main():
    try:
        restrict()
    except Exception:
        sys.stdout.write(
            json.dumps({"kind": "bootstrap_error", "code": "sandbox_setup_failed"})
            + "\n"
        )
        sys.stdout.flush()
        return 70
    sys.stdout.write('{"kind":"ready","protocol":1}\n')
    sys.stdout.flush()
    sys.path[:0] = ["/sdk", "/package"]
    os.chdir("/work")
    try:
        runpy.run_path("/package/" + sys.argv[1], run_name="__main__")
    except SystemExit as error:
        return (
            error.code
            if type(error.code) is int and 0 <= error.code <= 255
            else 1
            if error.code
            else 0
        )
    except BaseException:
        sys.stderr.write("Worker script failed.\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
