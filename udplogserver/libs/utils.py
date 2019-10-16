#coding: utf-8

import os
import sys
import json
import time
import socket
import threading
import traceback
import socketserver
import configparser
from urllib.parse import unquote
import ms71lib.client as ms71_cli

class UDPServer(socketserver.ThreadingMixIn, socketserver.UDPServer):
    """
    Threading UDP server class
    """

    daemon_threads = True
    allow_reuse_address = True
    socket_type = socket.SOCK_DGRAM
    #max_packet_size = 8192
    max_packet_size = 65536

    def __init__(self, server_address, RequestHandlerClass, log):
        super(UDPServer, self).__init__(server_address, RequestHandlerClass)
        self.log = log
        self.addr = server_address

    def finish_request(self, request, client_address):
        self.RequestHandlerClass(request, client_address,  self)

    def handle_error(self, request, client_address):
        self.log('='*16)
        self.log('Exception happened during processing of request from', client_address)
        self.log(traceback.format_exc()) # XXX But this goes to stderr!
        self.log('='*16)

class UDPHandler:
    """
    handler class for UDPServer
    """

    def __init__(self, request, client_address, server):
        self.request = request
        self.client_address = client_address
        self.server = server
        self.setup()
        try:
            self.handle()
        finally:
            self.finish()

    def setup(self):
        pass

    def handle(self):
        data = self.request[0].strip().decode()
        data = json.loads(data)
        sock = self.request[1]
        sys.APPCONF["queue"].put(data)

    def finish(self):
        pass


def qRead(interval=60, size=100000):
    """
    вычитываем длину очереди, и если там много элементов или подошло время, то даем задание на запись данных
    """
    start_time = time.time()
    key = ''
    with open('api.key', 'r') as f_obj:
        key = f_obj.read().strip()
    saveD = SaveData(base_type='text', 
    #saveD = SaveData(base_type='clickhouse', 
                     connect_args={"uri":"https://online365.pro/ch/", "verbose":False, "api_key":key}
                     )
    while True:
        try:
            time_dif = time.time() - start_time
            q_size = sys.APPCONF["queue"].qsize()
            if time_dif >= interval or q_size >= size:
                start_time = time.time()
                #формируем данные и отдаем их на запись
                full_data = []
                for i in range(q_size):
                    item = sys.APPCONF["queue"].get()
                    full_data.append(item)
                saveD.pull(full_data)
            time.sleep(1/100)
        except:
            traceback.print_exc()



class SaveData:

    def __init__(self, base_type=None, connect_args=None):
        self.base_type = base_type
        if self.base_type == 'clickhouse':
            print("saving to clickhouse", flush=True)
            self._send = self._ch
            self._ch_args = connect_args
            self._create_ch_tables()

        elif self.base_type == 'postgres':
            print("saving to postgres", flush=True)
            self._send = self._pg
            self._pg_args = connect_args
        elif self.base_type == 'firebird':
            print("saving to firebird", flush=True)
            self._send = self._fb
            self._fb_args = connect_args
        else:
            print("saving to stdout", flush=True)
            self._send = self._print

    def _create_ch_tables(self):
        sqls = [
        """CREATE DATABASE IF NOT EXISTS udp_logs;""",
        """CREATE TABLE IF NOT EXISTS udp_logs.logs
(
    application String,
    type String,
    user String,
    payload String,
    dt DateTime,
    date Date
) ENGINE = MergeTree(date, (dt, application, type), 8192);
  """
        ]
        server = ms71_cli.ServerProxy(**self._ch_args)
        
        request = server("request")
        for i in sqls:
            r = request(i.encode())
        server('close')

    def _make_connect(self):
        pass

    def _ch(self, full_data):
        server = ms71_cli.ServerProxy(**self._ch_args)  
        request = server("request")
        sql = "INSERT INTO udp_logs.logs FORMAT Values %s;"
        s = []
        #print("saving to clickhouse", flush=True)
        for i in full_data:
            try:
                s.append(f"""('{str(i[0])}', '{str(i[1])}', '{str(i[2])}', '{str(i[3]).replace("'", '"')}', '{str(i[4])}', '{str(i[4].split()[0])}') """)
                #for i in item:
                    
                    #print(i, type(i), sep="\t\t")
                #print(item, flush=True)
            except:
                pass
                print("-"*20, flush=True)
                print(i, flush=True)
                print("-"*20, flush=True)
        if len(s) > 0:
            sq = sql % ', '.join(s)
            #sq = sq.replace("'", '"')
            #print(sq)
            request(sq.encode())
        server("close")
                

    def _pg(self, full_data):
        #print("saving to postgres", flush=True)
        for item in full_data:
            print(item, flush=True)

    def _fb(self, full_data):
        #print("saving to firebird", flush=True)
        for item in full_data:
            print(item, flush=True)

    def _print(self, full_data):
        """
        записываем данные куда-то
        """
        for item in full_data:
            print(item, flush=True)

    def pull(self, full_data):
        threading.Thread(target=self._send, args=(full_data,), daemon=True).start()



def shutdown():
    """
    function runs when exiting
    """
    print("at exit function", flush=True)

def handle_commandline():
    args = []
    kwargs = {}
    sys.stdin.close()
    _argv = sys.argv[1:]
    for x in _argv:
        i = x.find('=')
        if i > -1:
            k, x  = x[:i], x[i+1:]
        else:
            k = None
        if k:
            v = unquote(x).split(',')
            if len(v) > 1:
                kwargs[unquote(k)] = tuple(_int(x) for x in v)
            else:
                kwargs[unquote(k)] = _int(v[0])
        else:
            if x:
                v = unquote(x).split(',')
                if len(v) > 1:
                    args.append(tuple(_int(x) for x in v))
                else:
                    args.append(_int(v[0]))
    return args, kwargs


def _int(x):
    try:
        fx = float(x)
        ix = int(fx)
        return ix if ix == fx else fx
    except:
        return x

    