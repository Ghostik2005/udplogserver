#coding: utf-8

__appname__ = 'udplogserver'
__version__ = '2018.271.1350'  # start the project
__version__ = '2025.364.0916'  # test

import sys
import time
import queue
import threading
import libs.utils as libs



"""
Логика работы:
собираем данные в очередь, либо в список, либо в словарь, либо в файл.
как только набирается много данных (например 100000 записей) или проходт время (например 1 минута) записываем данные
в clickhouse

"""


def main():
    sys.APPCONF = {
        "params": [],
        "kwargs": {},
        "addr": ("127.0.0.1", 0),
        }
    sys.APPCONF["queue"] = queue.Queue()
    sys.APPCONF["params"], sys.APPCONF["kwargs"] = libs.handle_commandline()
    sys.APPCONF["addr"] = sys.APPCONF["kwargs"].pop("addr", sys.APPCONF["addr"])
    rc = None
    try:
        prepare_server()
        udpserver = libs.UDPServer(("127.0.0.1", 4122), libs.UDPHandler, log=print)
        print(f'UDP server started at port {udpserver.addr[1]}', flush=True)
        udpserver.serve_forever()
    except KeyboardInterrupt:
        rc = 0
    except Exception as e:
        rc = str(e)
    finally:
        libs.shutdown()
    return str(rc)

def prepare_server():
    threads = []
    processes = []
    print(f'{__appname__} started at {time.strftime("%Y-%m-%d %H:%M:%S")}', flush=True)
    threads.append(threading.Thread(target=libs.qRead, kwargs={'interval':10, 'size':10000}, daemon=True))
    for th in threads:
        th.start()
    for pr in processes:
        pr.start()


if "__main__" == __name__:
    rc = main()
    if rc.isdecimal():
        sys.exit(int(rc))
    else:
        print(rc, flush=True)
        sys.exit(0)
