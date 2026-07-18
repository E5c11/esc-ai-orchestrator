import argparse
from pathlib import Path
from esc_exec.registry import default_registry_path
from esc_orchestrator.api import server
from esc_orchestrator.runtime import OpenCodeRuntime
from esc_orchestrator.scheduler import Scheduler
from esc_orchestrator.store import Store


def main(argv=None):
    parser=argparse.ArgumentParser(prog="esc-orchestrator")
    parser.add_argument("--host",default="127.0.0.1"); parser.add_argument("--port",type=int,default=8042)
    parser.add_argument("--db",type=Path,default=Path(".orchestrator/orchestrator.db"))
    parser.add_argument("--opencode",default="http://127.0.0.1:4097"); parser.add_argument("--registry",type=Path,default=default_registry_path())
    args=parser.parse_args(argv); store=Store(args.db); scheduler=Scheduler(store,OpenCodeRuntime(args.opencode,args.registry),args.registry); httpd=server(scheduler,store,args.host,args.port,args.registry)
    try: httpd.serve_forever()
    except KeyboardInterrupt: pass
    finally: scheduler.close(); httpd.server_close()
    return 0
