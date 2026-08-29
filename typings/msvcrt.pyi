LK_LOCK: int
LK_NBLCK: int
LK_UNLCK: int

def locking(fd: int, mode: int, length: int) -> None: ...
