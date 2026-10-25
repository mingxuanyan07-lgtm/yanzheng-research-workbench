from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
import qrcode, math, random, numpy as np
OUT=Path(r'D:\天猫AI\output\competition-promotion\20261021'); ASSETS=OUT/'assets'; ASSETS.mkdir(parents=True,exist_ok=True)
W,H=1920,1080
FONT_BOLD=r'C:\Windows\Fonts\Noto Sans SC Bold (TrueType).otf'; FONT_MED=r'C:\Windows\Fonts\Noto Sans SC Medium (TrueType).otf'; FONT_REG=r'C:\Windows\Fonts\NotoSansSC-VF.ttf'; FONT_LATIN=r'C:\Windows\Fonts\bahnschrift.ttf'
def font(sz,bold=False,medium=False,latin=False): return ImageFont.truetype(FONT_LATIN if latin else (FONT_BOLD if bold else (FONT_MED if medium else FONT_REG)),sz)
def text(d,xy,s,sz,fill=(244,248,252,255),bold=False,medium=False,latin=False,anchor=None,spacing=8): d.multiline_text(xy,s,font=font(sz,bold,medium,latin),fill=fill,anchor=anchor,spacing=spacing)
def gradient():
    a=Image.new('RGB',(1,H)); p=a.load()
    for y in range(H):
        t=y/(H-1); p[0,y]=tuple(int((4,12,25)[i]*(1-t)+(8,28,44)[i]*t) for i in range(3))
    return a.resize((W,H))
def radial(center,radius,color):
    y,x=np.ogrid[:H,:W]; dist=np.sqrt((x-center[0])**2+(y-center[1])**2); alpha=(np.clip(1-dist/radius,0,1)**2*color[3]).astype('uint8'); a=np.zeros((H,W,4),dtype='uint8'); a[...,0:3]=color[:3]; a[...,3]=alpha; return Image.fromarray(a,'RGBA')
def bg(seed=4):
    im=gradient().convert('RGBA'); im.alpha_composite(radial((1540,220),850,(0,194,220,52))); im.alpha_composite(radial((230,900),650,(77,102,255,24))); d=ImageDraw.Draw(im)
    for x in range(0,W,80): d.line((x,0,x,H),fill=(255,255,255,8),width=1)
    for y in range(0,H,80): d.line((0,y,W,y),fill=(255,255,255,8),width=1)
    r=random.Random(seed)
    for _ in range(85):
        x,y=r.randrange(W),r.randrange(H); z=r.choice([1,1,1,2]); d.ellipse((x-z,y-z,x+z,y+z),fill=(120,220,255,r.randrange(15,75)))
    return im
def dashed(d,pts,fill=(89,220,225,170),width=3,dash=18,gap=12):
    for a,b in zip(pts[:-1],pts[1:]):
        x1,y1=a; x2,y2=b; dist=math.hypot(x2-x1,y2-y1); n=int(dist/(dash+gap))+1
        for i in range(n):
            t1=i*(dash+gap)/dist; t2=min((i*(dash+gap)+dash)/dist,1); d.line(((x1+(x2-x1)*t1,y1+(y2-y1)*t1),(x1+(x2-x1)*t2,y1+(y2-y1)*t2)),fill=fill,width=width)
def rounded(im,size,radius=30):
    im=ImageOps.fit(im,size,method=Image.Resampling.LANCZOS,centering=(.5,.3)).convert('RGB'); mask=Image.new('L',size,0); ImageDraw.Draw(mask).rounded_rectangle((0,0,size[0]-1,size[1]-1),radius=radius,fill=255); im=im.convert('RGBA'); im.putalpha(mask); return im
def panel(im,box,radius=28,fill=(8,24,40,220),outline=(80,160,180,85),width=2):
    a=Image.new('RGBA',(box[2]-box[0],box[3]-box[1]),(0,0,0,0)); ImageDraw.Draw(a).rounded_rectangle((0,0,a.width-1,a.height-1),radius=radius,fill=fill,outline=outline,width=width); im.alpha_composite(a,(box[0],box[1]))
def pill(im,xy,s,fg=(213,251,255,255),fill=(11,72,88,225),outline=(73,211,225,150),size=28):
    d=ImageDraw.Draw(im); f=font(size,medium=True); b=d.textbbox((0,0),s,font=f); ww=b[2]-b[0]+44; hh=56; x,y=xy; d.rounded_rectangle((x,y,x+ww,y+hh),radius=hh//2,fill=fill,outline=outline,width=2); d.text((x+22,y+10),s,font=f,fill=fg); return x+ww
def save(im,name): im.convert('RGB').save(ASSETS/name,quality=95,optimize=True,progressive=True)
# 1 hero
s=Image.open(r'D:\天猫AI\output\competition-cover\研证-比赛封面-16比9-1920x1080.png').convert('RGBA'); d=ImageDraw.Draw(s); ov=Image.new('RGBA',(W,H),(0,0,0,0)); ImageDraw.Draw(ov).rectangle((0,0,W,110),fill=(1,9,17,110)); s.alpha_composite(ov); d.rounded_rectangle((58,32,624,86),radius=27,fill=(3,20,30,190),outline=(95,220,230,95),width=1); text(d,(87,45),'2026 天猫 AI 比赛  ·  效率进化',24,(226,250,253,255),medium=True); text(d,(1848,42),'RESEARCH  /  WITH  REASONS',20,(210,235,242,210),medium=True,latin=True,anchor='ra'); save(s,'01-研证主视觉.png')
# 2 friction
s=bg(9); d=ImageDraw.Draw(s); pill(s,(90,80),'THE REAL FRICTION  /  研究起步'); text(d,(90,210),'灵感很多，\n依据很少。',82,(246,250,252,255),bold=True,spacing=6); text(d,(98,455),'研究真正困难的，不是“想出题目”，\n而是判断它为什么值得做、下一步凭什么推进。',34,(150,184,196,255),medium=True,spacing=14); panel(s,(1090,150,1810,900),36); cx,cy=1450,515
for r,col in [(250,(58,194,211,45)),(180,(78,167,230,50)),(105,(150,225,230,65))]: d.ellipse((cx-r,cy-r,cx+r,cy+r),outline=col,width=2)
labels=[('问题',(1450,220),'明确研究对象'),('文献',(1650,370),'核对已有工作'),('证据',(1670,650),'记录实际阅读'),('判断',(1450,800),'由研究者选择'),('验证',(1215,650),'让结果回流')]
for lab,(x,y),sub in labels:
    d.ellipse((x-44,y-44,x+44,y+44),fill=(9,42,58,245),outline=(104,232,236,180),width=2); text(d,(x,y-1),lab[:1],28,(225,255,255,255),bold=True,anchor='mm'); text(d,(x,y+68),lab,27,(234,245,248,255),medium=True,anchor='ma'); text(d,(x,y+104),sub,18,(123,160,172,255),anchor='ma')
for a,b in [((1450,220),(1650,370)),((1650,370),(1670,650)),((1670,650),(1450,800)),((1450,800),(1215,650)),((1215,650),(1450,220))]: dashed(d,[a,b],fill=(68,194,214,100),width=2,dash=10,gap=9)
text(d,(1125,820),'让每个判断都能回看',27,(112,230,235,255),medium=True,anchor='ma'); save(s,'02-研究起步痛点.png')
# 3 loop
s=bg(11); d=ImageDraw.Draw(s); text(d,(90,80),'研证',86,(245,250,252,255),bold=True); text(d,(94,196),'把科研起步变成一条可追溯的证据链。',38,(140,220,232,255),medium=True); steps=[('01','问题澄清','Qwen 判断信息是否足够\n用 1–3 个问题对准研究对象',(83,207,219,255)),('02','文献对照','真实题录检索\n区分“命中文献”与“实际阅读”',(85,187,226,255)),('03','候选修订','依据与风险并列\n候选版本保留、人工选择',(111,164,249,255)),('04','验证回流','实验或理论核查\n结果重新进入研究记录',(123,228,192,255))]
for i,(num,titlev,desc,col) in enumerate(steps):
    x=90+i*448;y=330; panel(s,(x,y,x+402,y+530),34,fill=(8,24,39,230),outline=(col[0],col[1],col[2],100),width=2); text(d,(x+32,y+32),num,70,(col[0],col[1],col[2],95),bold=True,latin=True); d.rounded_rectangle((x+34,y+122,x+96,y+130),radius=4,fill=col); text(d,(x+34,y+164),titlev,40,(244,249,251,255),bold=True); text(d,(x+34,y+238),desc,27,(151,177,188,255),medium=True,spacing=15)
for i in range(3): dashed(d,[(90+i*448+410,594),(90+(i+1)*448-8,594)],fill=(82,215,224,155),width=3)
text(d,(900,925),'每个候选，都有出处；每次修订，都有理由。',29,(137,203,216,255),medium=True,anchor='ma'); save(s,'03-四步科研闭环.png')
# 4 qwen
s=bg(12); d=ImageDraw.Draw(s); pill(s,(90,72),'Qwen + ModelScope Skills'); text(d,(90,158),'模型负责建议，\n研究者保留决定。',67,(245,250,252,255),bold=True); text(d,(95,360),'千问参与问题澄清、候选生成与命题修订；\n两个公开 Skill 将方向探索和文献证据拆成明确边界。',30,(149,179,190,255),medium=True,spacing=13)
app=Image.open(r'D:\天猫AI\output\modelscope-public-access-20261021-154340\public-app-anonymous.png').convert('RGB'); shot=rounded(app,(920,565),32); panel(s,(860,280,1850,920),38,fill=(5,16,27,245),outline=(70,191,209,140),width=2); s.alpha_composite(shot,(912,304)); d.rounded_rectangle((890,290,970,320),radius=15,fill=(10,46,60,240),outline=(82,200,215,90)); text(d,(930,303),'LIVE',18,(190,250,250,255),medium=True,latin=True,anchor='mm')
for titlev,sub,desc,col,y in [('research-direction-guide','方向探索 Skill','澄清 → 候选 → 推荐 → 人工选择',(82,215,218,255),660),('literature-search','文献检索 Skill','题录 → 阅读范围 → 证据记录',(94,164,239,255),785)]:
    panel(s,(90,y,750,y+110),24,fill=(10,31,48,235),outline=(col[0],col[1],col[2],100),width=2); d.ellipse((112,y+35,142,y+65),fill=col); text(d,(162,y+14),sub,29,(239,247,250,255),bold=True); text(d,(162,y+56),desc,21,(140,172,184,255),medium=True)
text(d,(92,925),'AI 建议 ≠ 科学结论',28,(115,230,224,255),medium=True); text(d,(1480,925),'MODEL ASSISTS  /  HUMAN DECIDES',21,(121,165,178,255),medium=True,latin=True,anchor='ra'); save(s,'04-Qwen与Skills协作.png')
# 5 evidence
s=bg(13); d=ImageDraw.Draw(s); text(d,(90,70),'证据，不只是一条链接。',56,(245,250,252,255),bold=True); text(d,(94,160),'研证把“看到什么、读到哪里、为什么这样判断”拆开记录。',30,(142,207,219,255),medium=True); chain=[('01','来源','Crossref / arXiv\nDOI、作者、年份'),('02','阅读','摘要 / 全文\n版本与具体位置'),('03','摘录','精确笔记\nSHA256 快照'),('04','判断','支持 / 限制\n人工确认')]
for i,(num,t,sub) in enumerate(chain):
    x=90+i*445;y=265; panel(s,(x,y,x+398,y+210),26,fill=(9,29,44,230),outline=(71,181,201,80)); text(d,(x+25,y+20),num,22,(93,218,223,255),bold=True,latin=True); text(d,(x+25,y+62),t,39,(242,248,250,255),bold=True); text(d,(x+25,y+124),sub,23,(139,169,180,255),medium=True,spacing=9)
    if i<3: dashed(d,[(x+399,y+105),(x+438,y+105)],fill=(75,204,216,135),width=3,dash=8,gap=8)
proof=Image.open(r'D:\天猫AI\output\theory-review-integration\round-20261019-final-browser-20261019-194444\screenshots\03-verified-read-preview.png').convert('RGB'); shot=rounded(proof,(1130,460),26); panel(s,(682,550,1838,1005),28,fill=(2,9,17,245),outline=(56,152,176,90)); s.alpha_composite(shot,(718,572)); panel(s,(90,550,610,930),28,fill=(8,34,46,235),outline=(72,205,213,90)); text(d,(125,590),'“已验证”不等于\n“自然语言命题已证明”',35,(111,229,225,255),bold=True,spacing=13); text(d,(125,720),'程序保留来源、版本、摘录与\n哈希快照；语义是否等价仍需\n研究者与独立审阅。',24,(159,184,194,255),medium=True,spacing=14); text(d,(1845,1027),'TRACEABLE  /  REVIEWABLE  /  REVERSIBLE',18,(105,148,160,255),medium=True,latin=True,anchor='ra'); save(s,'05-证据可追溯.png')
# 6 human choice and verify
s=bg(14); d=ImageDraw.Draw(s); text(d,(90,70),'AI 给出建议，\n程序留下事实。',64,(245,250,252,255),bold=True,spacing=5); text(d,(95,270),'候选方向、模型提议和实验记录都有版本与上下文；\n只有研究者明确接受，修订才会写入当前研究。',29,(147,188,199,255),medium=True,spacing=13)
items=[('方向建议','保存候选版本与推荐理由','等待人工选择'),('理论修订','展示前后差异、证明路线与缺口','接受后才更新'),('实验 / Lean','独立运行并保留真实状态','结果回流记录')]
for i,(a,b,c) in enumerate(items):
    y=500+i*130; panel(s,(90,y,1000,y+105),22,fill=(9,27,43,230),outline=(69,179,199,85)); d.ellipse((112,y+36,142,y+66),fill=[(82,216,220,255),(96,164,239,255),(116,225,188,255)][i]); text(d,(164,y+16),a,27,(240,248,250,255),bold=True); text(d,(164,y+58),b,21,(135,165,176,255),medium=True); text(d,(970,y+54),c,20,(94,219,217,255),medium=True,anchor='ra')
ui=Image.open(r'D:\天猫AI\output\direction-closeout-20261020\verified-evidence\browser-passed\accept-after.png').convert('RGB').crop((90,250,1340,1080)); shot=rounded(ui,(790,680),28); panel(s,(1050,240,1840,920),32,fill=(5,16,26,245),outline=(66,170,192,95)); s.alpha_composite(shot,(1081,266)); d.rounded_rectangle((1115,293,1435,341),radius=24,fill=(10,61,76,230),outline=(89,224,226,110)); text(d,(1136,307),'人工选择已记录',21,(214,252,250,255),medium=True); text(d,(90,1000),'不是“AI 自动替你研究”，而是让每一次研究判断都更可解释。',27,(122,218,224,255),medium=True); save(s,'06-人工判断与验证.png')
# 7 CTA qr
s=bg(15); d=ImageDraw.Draw(s); text(d,(90,105),'研证',108,(245,250,252,255),bold=True); text(d,(96,235),'从灵感到课题的\nAI 科研工作台',53,(220,242,246,255),bold=True,spacing=9); text(d,(100,408),'让研究有方向，让想法有依据。',32,(105,229,225,255),medium=True)
for i,ftext in enumerate(['Qwen 研究澄清','ModelScope Skills','证据链与人工确认']): pill(s,(100,500+i*74),ftext,size=24,fill=(9,45,59,235))
text(d,(100,820),'QUICK START',23,(112,170,183,255),medium=True,latin=True); d.rounded_rectangle((100,860,580,940),radius=30,fill=(100,230,216,255)); text(d,(340,900),'modelscope.cn  ·  研证在线工作台',24,(3,28,34,255),medium=True,anchor='mm')
links=[('在线体验','Public Demo','https://modelscope.cn/studios/zxyymx071117/yanzheng-research-demo?mode=full'),('方向探索','Research Skill','https://modelscope.cn/skills/zxyymx071117/research-direction-guide'),('文献证据','Literature Skill','https://modelscope.cn/skills/zxyymx071117/literature-search')]
for i,(label,sub,url) in enumerate(links):
    x=730+i*380; panel(s,(x,185,x+330,865),30,fill=(247,250,251,245),outline=(216,248,246,255)); text(d,(x+165,225),label,32,(9,40,48,255),bold=True,anchor='ma'); text(d,(x+165,272),sub,18,(45,104,111,255),medium=True,latin=True,anchor='ma'); qr=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,box_size=8,border=2); qr.add_data(url); qr.make(fit=True); qim=qr.make_image(fill_color=(5,20,27),back_color='white').convert('RGB').resize((240,240),Image.Resampling.NEAREST); s.alpha_composite(qim.convert('RGBA'),(x+45,335)); text(d,(x+165,620),'扫码打开',24,(9,40,48,255),bold=True,anchor='ma'); text(d,(x+165,665),'Qwen × 科研提效',18,(58,116,121,255),medium=True,anchor='ma')
text(d,(1840,1020),'2026 天猫 AI 比赛  ·  效率进化赛道  ·  特别赛题',18,(121,164,175,255),medium=True,anchor='ra'); save(s,'07-在线体验入口.png')
# 8 article hero
s=bg(17); d=ImageDraw.Draw(s); d.ellipse((1250,-220,2200,730),fill=(35,190,220,24)); shot=rounded(Image.open(r'D:\天猫AI\output\modelscope-public-access-20261021-154340\public-app-anonymous.png').convert('RGB'),(800,450),30); panel(s,(1020,300,1870,820),34,fill=(5,16,27,245),outline=(73,211,221,120)); s.alpha_composite(shot,(1045,322)); text(d,(90,115),'研证',100,(247,250,252,255),bold=True); text(d,(95,245),'从灵感到课题的\nAI 科研工作台',57,(225,245,248,255),bold=True,spacing=9); text(d,(100,425),'让研究判断，有据可循。',36,(94,230,220,255),medium=True); text(d,(100,520),'Qwen  ·  ModelScope Skills  ·  证据链与人工确认',25,(142,179,190,255),medium=True)
for i,(a,b) in enumerate([('想法澄清','01'),('文献对照','02'),('验证回溯','03')]):
    y=650+i*95; d.rounded_rectangle((100,y,650,y+72),radius=22,fill=(8,31,47,225),outline=(72,166,187,85)); text(d,(122,y+20),f'{b}  {a}',26,(224,244,247,255),medium=True)
text(d,(1835,1025),'RESEARCH, WITH REASONS.',19,(110,159,170,255),medium=True,latin=True,anchor='ra'); save(s,'08-图文头图.png')
for p in sorted(ASSETS.glob('*.png')):
    im=Image.open(p); im.convert('RGB').save(ASSETS/(p.stem+'.jpg'),quality=91,optimize=True,progressive=True)
print('generated',len(list(ASSETS.glob('*.png'))),'PNG slides')
for p in sorted(ASSETS.glob('*')): print(p.name,p.stat().st_size)
