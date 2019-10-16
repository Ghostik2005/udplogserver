#!/usr/bin/env python
# coding: utf-8

__version__ = "2016-07-22 0033"

import sys, os, time, sqlite3, threading
PY3 = sys.version_info[0] == 3

if PY3:
    import queue as Queue
    raw_input = input
else:
    import Queue

def lock_open(lock_path=None):
    pid = os.getpid()
    if not lock_path:
        lock_path = os.path.splitext(os.path.basename(__file__))[0] + '.lock'

    isLocked = False
    con=cur=None
    try:
        con = sqlite3.connect(lock_path, timeout=0.2, isolation_level='EXCLUSIVE')
        cur = con.cursor()
        cur.execute('PRAGMA journal_mode = MEMORY;')
        cur.execute('PRAGMA synchronous = OFF;')
        cur.execute(u'select * from PID')
    except sqlite3.OperationalError as e:
        if e.args[0].lower().find('no such table:')>-1:
            sql = u"""PRAGMA journal_mode = MEMORY;"""
            cur.execute(sql)
            sql = u"""PRAGMA synchronous = OFF;"""
            cur.execute(sql)
            sql = u"""CREATE TABLE PID (ID INTEGER NOT NULL, DT TIMESTAMP NOT NULL DEFAULT CURRENCY_TIMESTAMP);"""
            cur.execute(sql)
            con.commit()
        elif e.args[0].lower().find('database is locked')>-1:
            isLocked = True
        else:
            print(e.__class__, e)
    except Exception as e:
        print(e.__class__, e)
    finally:
        if isLocked:
            if con:
                con.close()
            con=cur=None
        else:
            cur.execute(u'insert into pid(id)values(?)', (pid,))
    if isLocked:
        return None
    else:
        return [pid, lock_path, con, cur]

def lock_close(lock):
    pid, lock_path, con, cur = lock
    while lock:
        lock.pop()
    if cur:
        try: cur.close()
        except: pass
    if con:
        try: con.close()
        except: pass
    try: os.remove(lock_path)
    except: pass

def start(func, *a, **kw):
    def _f(cb):
        try:
            result, error = func(*a, **kw), None
        except Exception as e:
            result, error = None, e
        cb(result, error)
    cb = _ThreadResult()
    th = threading.Thread(target=_f, args=(cb,))
    th.daemon = True
    th.start()
    return cb

def startone(func, *a, **kw):
    g = _startone(func, [a, kw])
    if PY3:
        lock = g.__next__()
    else:
        lock = g.next()
    def _f(cb):
        try:
            if PY3:
                result, error = g.__next__(), None
            else:
                result, error = g.next(), None
        except Exception as e:
            result, error = None, e
        try:
            if PY3:
                g.__next__()
            else:
                g.next()
        except StopIteration as e:
            pass
        cb(result, error)
    if lock:
        cb = _ThreadResult()
        th = threading.Thread(target=_f, args=(cb,))
        th.daemon = True
        th.start()
        return cb
    else:
        try:
            if PY3:
                g.__next__()
            else:
                g.next()
        except StopIteration as e:
            pass
        return None

def _startone(func, params):
    nm = func.__func__.__name__ if hasattr(func, "__func__") else func.__name__
    lock = lock_open(nm + ".lock")
    yield lock
    if lock:
        try:
            yield func(*params[0], **params[1])
        finally:
            lock_close(lock)

class _ThreadResult(object):

    def __init__(self):
        self.__result = None
        self.__error = None
        self.__isWait = False
        self.__qWait = Queue.Queue(1)
        self.__isReady = 0
        self.__resolve = None
        self.__reject = None

    def wait(self):
        try:
            if self.__isReady < 1:
                self.__isWait = True
                self.__qWait.get()
                self.__qWait.task_done()
        finally:
            self.__isWait = False

    def done(self, resolve, reject=None):
        if resolve:
            self.__resolve = resolve
        else:
            self.__resolve = lambda x: None
        if reject:
            self.__reject = reject
        else:
            self.__reject = lambda x: None
        self.__done()

    def __call__(self, r, e):
        self.__result = r
        self.__error = e
        self.__isReady = 1
        self.__done()

    def __done(self):
        if self.__isReady == 1:
            if self.__error:
                if self.__reject:
                    self.__isReady = 2
                    self.__reject(self.__error)
            else:
                if self.__resolve:
                    self.__isReady = 2
                    self.__resolve(self.__result)
            if self.__isWait:
                self.__qWait.put(1)


########################################################################

def myfunc(*a, **kw):
    c = 0
    while c < 5:
        c += 1
        print("I'm working", c, a, kw)
        sys.stdout.flush()
        time.sleep(1)
    #raise RuntimeError("DOME")
    return "DONE"

if __name__ == '__main__':
    def r_myfunc(r):
        print("R>>>", r)

    def e_myfunc(e):
        print("E>>>", e)

    res = startone(myfunc, 1, "2", [3, 5], var6=6, var7=7)
    if res:
        #res.wait()
        res.done(r_myfunc, e_myfunc)
        #print(111); sys.stdout.flush()
        raw_input("press [enter] key...\n")
    else:
        print("stop: this is worked")
        sys.stdout.flush()
