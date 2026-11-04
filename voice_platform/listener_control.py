from threading import Event


class ListenerStopped(Exception):
    pass


class ListenerControl:
    """Cooperative stop signal and optional device event sink."""

    def __init__(self, report=None):
        self.stop = Event()
        self.report = report or (lambda state, detail: None)

    def check(self):
        if self.stop.is_set():
            raise ListenerStopped()

    def emit(self, state, detail=""):
        self.check()
        self.report(state, detail)
