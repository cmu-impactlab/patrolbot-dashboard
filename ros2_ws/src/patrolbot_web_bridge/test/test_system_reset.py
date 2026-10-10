import json
import socket
import threading
from uuid import uuid4
import pytest
from patrolbot_web_bridge.system_reset import SystemResetClient, exchange

class WS:
    def __init__(self):
        self.sent=[]; self.stats={'last_connect_mono':1}; self.connected=threading.Event();self.connected.set()
    def send(self,kind,data):self.sent.append((kind,data))

def client(transport):
    ws=WS();return SystemResetClient('/unused',ws,transport=transport),ws

def test_offline_and_unauthorized_never_submit():
    calls=[];c,w=client(lambda *a:calls.append(a));rid=str(uuid4())
    c.request({'command_id':rid,'operator_authorized':True})
    c.available=True;c.request({'command_id':rid,'operator_authorized':False})
    assert not calls and all(not r['accepted'] for _,r in w.sent)

def test_singleflight_durable_status_and_reconnect():
    rid=str(uuid4());state={'ok':True,'state':'idle'}
    c,w=client(lambda *a:state);c.poll();assert not c.busy
    c.request({'command_id':rid,'operator_authorized':True})
    c.request({'command_id':str(uuid4()),'operator_authorized':True})
    assert c._requests.get_nowait()==rid and w.sent[-1][1]['accepted'] is False
    state.update(request_id=rid,state='running');c._submit(rid)
    assert c.busy and any(k=='command.ack' and v['accepted'] for k,v in w.sent)
    state['state']='succeeded';state['message']='Software restarted; set a fresh pose.'
    c.poll();assert not c.busy and w.sent[-1][1]['outcome']=='succeeded'
    count=len(w.sent);c.poll();assert len(w.sent)==count
    w.stats['last_connect_mono']=2;c.poll();assert len(w.sent)==count+1

def test_ambiguous_submission_never_retries_or_releases_on_idle():
    calls=[]
    def fail(path,payload):calls.append(payload);raise TimeoutError()
    c,w=client(fail);c.available=True;c.busy=False
    rid=str(uuid4());c.request({'command_id':rid,'operator_authorized':True});c._submit(c._requests.get_nowait())
    assert c.busy and w.sent[-1][0]=='command.progress'
    c.transport=lambda *a:{'ok':True,'state':'idle'};c.poll()
    assert c.busy and len(calls)==1

def test_terminal_uncertain_blocks_next_command():
    rid=str(uuid4());c,w=client(lambda *a:{'ok':True,'state':'uncertain','request_id':rid})
    c.poll();assert c.busy and w.sent[-1][1]['outcome']=='timeout'

def test_transport_bounds_reply(tmp_path):
    path=str(tmp_path/'socket');server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);server.bind(path);server.listen()
    def serve():
        conn,_=server.accept()
        with conn:conn.recv(4096);conn.sendall(b'x'*8193)
        server.close()
    t=threading.Thread(target=serve);t.start()
    with pytest.raises(ValueError):exchange(path,{'op':'current'})
    t.join()


def test_previous_terminal_status_cannot_release_new_queued_request():
    previous=str(uuid4());new=str(uuid4())
    c,w=client(lambda *a:{'ok':True,'state':'succeeded','request_id':previous})
    c.poll();assert not c.busy
    c.request({'command_id':new,'operator_authorized':True});c.poll()
    assert c.busy and c._pending_id==new
