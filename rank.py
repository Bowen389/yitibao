"""Combine dated upstream JSON reports with deterministic, auditable rules."""
import argparse
from datetime import date, datetime
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

VERSION = 'v1'
SOURCES = {
    'zonghe': ('P2','H1','S2_1','S2_2','S2_3','S2_4','HGB'),
    'top': ('lh','n3','v3','s2','bf','fb'),
    's9-duoyinzi': ('champion','balanced','runner','original','wild','B0','K3','K5'),
}
ENTRY = {
    'P2': '开盘缺口 (−5%, −2%]，按缺口选首位',
    'FB': 'C2 [−2.5%, +5%] 优先；C1 [−5%, −2%]，同档等开盘排序',
    '机构': '复核停牌及开盘涨跌停',
    '趋势': '复核对应策略的开盘缺口及正式名额',
    'HGB': 'Top3 复核开盘可成交性',
    'BF/G3': '开盘按 BF、G3 原顺位和条件复核',
    'S9股票': '复核上游对应版本的开盘执行条件',
    'ETF': '核对 ETF 上游门控、持仓及目标权重',
}

def rank_points(base, rank):
    return base - (0 if rank == 1 else 4 if rank <= 3 else 8 if rank <= 5 else 14)

def load_report(repo, day, fixture):
    if fixture:
        return json.loads((fixture/f'{repo}.json').read_text())
    url = f'https://raw.githubusercontent.com/Bowen389/{repo}/main/reports/{day}.json'
    with urlopen(Request(url,headers={'User-Agent':'yitibao-ranking'}),timeout=15) as response:
        return json.load(response)

def trade_day(day):
    import baostock as bs
    login=bs.login()
    if login.error_code!='0':
        raise RuntimeError(login.error_msg)
    try:
        result=bs.query_trade_dates(start_date=day,end_date=day)
        if result.error_code!='0' or not result.next():
            raise RuntimeError('无法核实当日交易日历')
        row=dict(zip(result.fields,result.get_row_data()))
        return row['is_trading_day']=='1'
    finally:
        bs.logout()

def collect(day, fixture):
    hits, health, sessions = [], {}, set()
    for repo, expected in SOURCES.items():
        issues = []
        health[repo] = issues
        try:
            report = load_report(repo,day,fixture)
            if report.get('date',report.get('asof'))!=day:
                raise ValueError('信号日期不符')
            data=report['strategies']
            if not isinstance(data,dict):
                raise ValueError('strategies 格式异常')
            next_day=report.get('next_session')
            if next_day and date.fromisoformat(next_day)>date.fromisoformat(day):
                sessions.add(next_day)
        except (OSError,KeyError,ValueError,TypeError) as error:
            issues.append(f'报告缺失或不可用：{error}')
            continue

        for key in expected:
            section=data.get(key)
            if not isinstance(section,dict) or section.get('error') or section.get('status') in ('数据异常','error','failed') or (repo=='s9-duoyinzi' and (section.get('status')!='ok' or section.get('asof')!=day or (report.get('errors') or {}).get(key))):
                issues.append(f'{key} 缺失或异常')
                continue
            if section.get('provisional'):
                issues.append(f'{key} 为暂定结果')
            exec_day=section.get('exec_day')
            if exec_day and date.fromisoformat(exec_day)>date.fromisoformat(day):
                sessions.add(exec_day)
            rows=section.get('picks' if repo=='s9-duoyinzi' else 'candidates')
            if not isinstance(rows,list):
                issues.append(f'{key} 候选格式异常')
                continue
            spec=[]
            if repo=='zonghe':
                pending=section.get('status')=='待开盘'
                for rank,row in enumerate(rows,1):
                    if key=='P2':
                        family,points,eligible,position='P2',61,pending,None
                    elif key=='H1':
                        family='FB' if str(row.get('route','')).startswith('FB') else 'BF/G3'
                        points,eligible,position=(62 if family=='FB' else 69),pending,None
                    elif key=='HGB':
                        family,points,eligible,position='HGB',rank_points(69,rank),pending,rank
                    else:
                        family,points,eligible,position='趋势',rank_points(76,rank),pending and rank==1,rank
                    spec.append((row,family,points,eligible,position))
            elif repo=='top':
                family={'lh':'机构','n3':'机构','v3':'趋势','s2':'趋势','bf':'BF/G3','fb':'FB'}[key]
                meta=section.get('meta') or {}
                for rank,row in enumerate(rows,1):
                    if family=='机构':
                        points=75+4*(row.get('volume_tag')=='符合量价')+3*((row.get('qualifying_amount_yuan') or 0)>=100_000_000)
                        spec.append((row,family,points,True,None))
                    elif key=='fb':
                        spec.append((row,family,62,True,None))
                    else:
                        spec.append((row,family,rank_points(72,rank),True,rank))
                if key=='v3' and meta.get('gate_allow') is False:
                    raw=sorted(meta.get('raw_candidates') or [],key=lambda x:(-x.get('strength',0),-x.get('gain',0),x['code']))
                    spec.extend((row,family,rank_points(70,rank),False,rank) for rank,row in enumerate(raw[:5],1))
            else:
                family='ETF' if key in ('B0','K3','K5') else 'S9股票'
                spec.extend((row,family,rank_points(75,rank),section.get('gate_on') is True,rank) for rank,row in enumerate(rows,1))
            for row,family,points,eligible,rank in spec:
                code=str(row.get('code',''))
                if not re.fullmatch(r'\d{6}',code):
                    issues.append(f'{key} 存在无效股票代码')
                    continue
                hits.append(dict(code=code,name=row.get('name') or code,family=family,points=points,
                                 pending=eligible and not section.get('provisional'),rank=rank,
                                 source=f'{repo}/{key}',route=row.get('route'),
                                 detail={k:row[k] for k in ('qualifying_amount_yuan','volume_ratio_20','volume_tag','pos60_med','score') if row.get(k) is not None}))
    if len(sessions)>1:
        for issues in health.values():
            issues.append('上游次日日期冲突，所有候选降为观察')
        for hit in hits:
            hit['pending']=False
    return hits, health, next(iter(sessions)) if len(sessions)==1 else None

def score(hits):
    groups={}
    for hit in hits:
        groups.setdefault((hit['family']=='ETF',hit['code']),[]).append(hit)
    boards={'pending':[],'watch':[],'etf':[]}
    for (is_etf,code), evidence in groups.items():
        pending=any(h['pending'] for h in evidence)
        family_scores={}
        for h in evidence:
            if pending and not h['pending']:
                continue
            key=h['family']
            family_scores[key]=max(family_scores.get(key,0),h['points'])
        bonus=min(20,10*(len(family_scores)-1))
        points=min(100,max(family_scores.values())+bonus)
        board='etf' if is_etf else 'pending' if pending else 'watch'
        boards[board].append(dict(code=code,name=evidence[0]['name'],score=points,pending=pending,
                                  families=family_scores,bonus=bonus,evidence=evidence))
    for board in boards.values():
        board.sort(key=lambda row:(-row['pending'],-row['score'],-len(row['families']),row['code']))
    return boards

def publish(day, next_day, boards, health, output):
    status='partial' if any(health.values()) else 'complete'
    result=dict(date=day,next_session=next_day,status=status,scoring_version=VERSION,
                sources=health,boards=boards)
    label='部分汇总' if status=='partial' else '三包齐全'
    lines=[f'# {day} 一体包候选评分','',f'次一交易日 **{next_day or "待核实"}** · **{label}** · 评分 {VERSION}。','']
    for repo,issues in health.items():
        lines.append(f'- {repo} · '+('；'.join(issues).replace('\n',' ') if issues else '已读取同日结果'))
    lines.extend(['','同族取最高分。最高家族分加不同家族共振分，每多一家 +10，最多 +20。待确认榜只计待确认信号。',''])
    for key,title in [('pending','次日待确认'),('watch','仅观察'),('etf','ETF 单列')]:
        lines.extend([f'## {title}',''])
        if not boards[key]:
            lines.extend(['暂无候选。',''])
            continue
        lines.extend(['| 排名 | 代码 | 名称 | 综合分 | 分数拆解 | 信号来源 | 开盘复核 |','|---:|---|---|---:|---|---|---|'])
        for i,row in enumerate(boards[key][:10],1):
            name=str(row['name']).replace('|','\\|').replace('\n',' ')
            breakdown='、'.join(f'{family} {points}' for family,points in row['families'].items())
            if row['bonus']:
                breakdown+=f'，共振 +{row["bonus"]}'
            evidence='、'.join(h['source']+(f' #{h["rank"]}' if h['rank'] else '')+
                              ('（观察）' if row['pending'] and not h['pending'] else '') for h in row['evidence'])
            entry='；'.join(ENTRY[k] for k in row['families']) if row['pending'] else '仅观察'
            lines.append(f'| {i} | {row["code"]} | {name} | {row["score"]} | {breakdown} | {evidence} | {entry} |')
        lines.append('')
    lines.append('分数为盘前排序规则分。各策略的买入条件和名额以原包为准。完整候选见同日 JSON。\n')
    output.mkdir(parents=True,exist_ok=True)
    latest=output/'latest.json'
    newest=json.loads(latest.read_text()) if latest.exists() else {}
    dated=output/f'{day}.json'
    existing=json.loads(dated.read_text()) if dated.exists() else {}
    if existing.get('status')=='complete' and status=='partial':
        print('保留该日已有的完整报告')
        return
    if existing and not any(boards.values()) and status=='partial':
        print('保留该日已有候选，当前无可用候选输入')
        return
    for stem in ([day,'latest'] if newest.get('date','')<=day else [day]):
        (output/f'{stem}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
        (output/f'{stem}.md').write_text('\n'.join(lines))
    print(f'{day}: {label}，'+', '.join(f'{k}={len(v)}' for k,v in boards.items()))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--date',type=date.fromisoformat)
    parser.add_argument('--fixture',type=Path)
    parser.add_argument('--output',type=Path,default=Path('reports'))
    args=parser.parse_args()
    day=(args.date or datetime.now(ZoneInfo('Asia/Shanghai')).date()).isoformat()
    if not args.date and not trade_day(day):
        print(f'{day} 休市')
        return
    hits,health,next_day=collect(day,args.fixture)
    publish(day,next_day,score(hits),health,args.output)

if __name__=='__main__':
    main()
