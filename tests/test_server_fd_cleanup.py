import threading

from stepwise.server import _ThreadLocalConnProxy


def test_thread_local_store_prunes_connections_from_dead_threads(tmp_path):
    proxy = _ThreadLocalConnProxy(str(tmp_path / "stepwise.db"))

    def touch_store() -> None:
        proxy.execute("SELECT 1").fetchone()

    first = threading.Thread(target=touch_store)
    first.start()
    first.join()
    assert len(proxy._all_conns) == 1

    second = threading.Thread(target=touch_store)
    second.start()
    second.join()

    assert len(proxy._all_conns) == 1
    proxy.close_all()
