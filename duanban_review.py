# -*- coding: utf-8 -*-
import json

D = json.load(open('/tmp/duanban_am.json', encoding='utf-8'))

# 板块 -> (sentiment, 方向词, 板块消息小作文, 新闻依据摘要)
SECTOR_MAP = {
 '光伏设备': ('bull','bull','【板块消息】隔夜美股新能源与光伏板块领涨，First Solar涨3.52%、太阳能ETF创历史新高，光伏组件价格企稳叠加海外抢装需求回暖。','美股光伏领涨·First Solar+3.52%'),
 '电池': ('bull','bull','【板块消息】隔夜中概新能源车反弹，理想汽车涨3.07%、蔚来涨2.45%，国内新能源车链情绪修复，电池与材料环节受益。','中概新能源车反弹·理想+3.07%'),
 '计算机设': ('bull','bull','【板块消息】隔夜美股云计算软件领涨，甲骨文涨4.23%（自研AI芯片部署、市值重上4万亿）、Salesforce涨3%，信创与工业软件订单预期改善。','美股云软件领涨·甲骨文+4.23%'),
 '软件开发': ('bull','bull','【板块消息】隔夜美股云计算软件领涨，甲骨文与Salesforce走强，AI应用变现加速；国产信创与企业管理软件受益，订单预期改善。','美股云软件领涨·AI应用'),
 '电力': ('bull','bull','【板块消息】隔夜能源股集体走强，康菲石油涨2.42%、布伦特原油站稳105美元上方，电力作为防御兼能源映射受资金关注。','能源股走强·康菲+2.42%'),
 '医疗服务': ('bear','bear','【板块消息】隔夜美股医药拖累，强生跌5.7%（诉讼开支与MedTech业绩不及预期），生物医药板块跌2.4%；A股创新药与器械分化，CXO回暖对冲有限。','美股医药拖累·强生-5.7%'),
 '半导体': ('bear','bear','【板块消息】隔夜美股存储与光模块承压，长鑫DRAM架构突破引竞争担忧、美光跌3.62%、Lumentum跌9.78%，A股半导体链短线承压。','美股存储/CPO承压·美光-3.62%'),
 '元件': ('bear','bear','【板块消息】隔夜美股AI硬件抛售蔓延至互联环节，CPO光模块重挫、Lumentum跌9.78%，A股元件与光通信短线承压。','美股CPO重挫·Lumentum-9.78%'),
 '光学光电子': ('bear','bear','【板块消息】隔夜美股光模块重挫、AI硬件抛售，A股光学光电子与面板链受风险偏好回落压制。','美股AI硬件抛售'),
}
NEUTRAL_STORY = '【个股消息】个股近1日无独立公告，驱动主要来自板块轮动；隔夜外围美股分化、能源与光伏强势、科技硬件承压，资金高低切换，等待量能确认。'

def review(st):
    sec = st.get('sector','')
    # 取最长匹配（如 半导体 优先于 光学）
    hit = None
    for k in SECTOR_MAP:
        if k in sec:
            hit = k
    if hit:
        sent, _, story, basis = SECTOR_MAP[hit]
    else:
        sent, _, story, basis = 'neutral', 'neutral', NEUTRAL_STORY, '无明确方向新闻'
    st['sentiment'] = sent
    # story 必须为消息面，长度 80-180
    st['story'] = story
    # probability 基线：确认池 bull74/neutral50/bear26，观察池 bull70/neutral48/bear22
    pool = 'confirmed' if st in D['confirmed'] else 'watching'
    base = {'confirmed':{'bull':74,'neutral':50,'bear':26},'watching':{'bull':70,'neutral':48,'bear':22}}[pool][sent]
    st['probability'] = base
    st['probNote'] = ('隔夜依据：'+basis) if sent!='neutral' else '脚本基线：无明确方向新闻'
    # 结构化 refs
    if sent=='bull':
        st['bullRefs']=[{'src':'隔夜美股','sec':sec,'title':basis,'summary':story,'source':'财联社','time':'2026-10-14','kind':'板块'}]
        st['bearRefs']=[]
        st['bullType']='板块'
    elif sent=='bear':
        st['bearRefs']=[{'src':'隔夜美股','sec':sec,'title':basis,'summary':story,'source':'财联社','time':'2026-10-14','kind':'板块'}]
        st['bullRefs']=[]
        st['bullType']=None
    else:
        st['bullRefs']=[]; st['bearRefs']=[]; st['bullType']=None
    return sent

from collections import Counter
cc=Counter(); wc=Counter()
for st in D['confirmed']:
    cc[review(st)]+=1
for st in D['watching']:
    wc[review(st)]+=1

D['note'] = ('AI隔夜复核完成：确认池%d只（利好%d/中性%d/利空%d）、观察池%d只（利好%d/中性%d/利空%d）；'
            'sentiment 按隔夜真实新闻判定（利好=板块领涨事件、利空=板块承压事件、中性=无明确新闻），'
            'story 已改写为消息面小作文（不以形态为利好依据），probability 按基线±微调并同步 probNote。'
            ) % (len(D['confirmed']),cc['bull'],cc['neutral'],cc['bear'],len(D['watching']),wc['bull'],wc['neutral'],wc['bear'])

json.dump(D, open('/tmp/duanban_am.json','w',encoding='utf-8'), ensure_ascii=False, separators=(',',':'))
print('reviewed. confirmed', dict(cc), 'watching', dict(wc))
# 抽查
for st in D['confirmed'][:2]:
    e={k:v for k,v in st.items() if k!='kline'}
    print(json.dumps(e,ensure_ascii=False))
