"""Retry Windows sharing locks without replacing the last valid snapshot early."""
import time


def replace_snapshot(temporary,destination):
    for attempt in range(10):
        try:
            temporary.replace(destination)
            return
        except PermissionError:
            if attempt==9:raise
            time.sleep(min(.05*(2**attempt),.5))
