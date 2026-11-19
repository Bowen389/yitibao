"""Refresh an aggregate when dated upstream reports change; never dispatch another repo."""
import argparse
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from exchange_calendars import get_calendar


def signatures(day, sources):
    parts=[]
    for repo,path in sources:
        try:
            request=Request(f'https://raw.githubusercontent.com/Bowen389/{repo}/main/{path.format(day=day)}',headers={'User-Agent':'aggregate-recovery','Cache-Control':'no-cache'})
            with urlopen(request,timeout=15) as response:
                parts.append(sha256(response.read()).hexdigest())
        except Exception as exc:
            parts.append(type(exc).__name__)
    return tuple(parts)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--date')
    parser.add_argument('--wait-seconds',type=int,default=0)
    args=parser.parse_args()
    now=datetime.now(ZoneInfo('Asia/Shanghai'))
    cutoff=now.date()-timedelta(days=int(now.hour<15))
    day=args.date or str(get_calendar('XSHG').date_to_session(str(cutoff),direction='previous').date())
    if day>str(cutoff):
        parser.error('请选择已收盘日期')
    is_dayitong=Path('daily.py').exists()
    sources=[('top','reports/{day}.json'),('zonghe','reports/{day}.json'),('s9-duoyinzi','reports/{day}.json')]
    if is_dayitong:
        sources += [('ceshi','reports/state.json'),('zhulizhuoyao','reports/{day}-close.json'),('2j3','state/{day}.json')]
    command=[sys.executable,'daily.py' if is_dayitong else 'rank.py','--date',day]
    deadline=time.monotonic()+max(0,args.wait_seconds)
    previous=None
    last_build=0
    while True:
        signature=signatures(day,sources)
        if signature!=previous or time.monotonic()-last_build>=300:
            result=subprocess.run(command,check=False)
            previous=signature
            last_build=time.monotonic()
            print(f'{day}: upstream refresh, exit={result.returncode}',flush=True)
        path=Path('reports')/(day+'.json')
        try:
            report=json.loads(path.read_text())
            if report.get('date')==day and report.get('status')=='complete':
                print('All required sources and report checks complete',flush=True)
                return 0
        except (OSError,ValueError):
            pass
        remaining=deadline-time.monotonic()
        if remaining<=0:
            print('PARTIAL: current report saved; upstream/price completeness still unresolved',flush=True)
            return 2
        time.sleep(min(60,remaining))

if __name__=='__main__':
    raise SystemExit(main())
