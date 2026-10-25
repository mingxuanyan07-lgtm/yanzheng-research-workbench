from pathlib import Path
import wave, subprocess, json, math, os
import numpy as np
from scipy.signal import butter, lfilter
OUT=Path(r'D:\天猫AI\output\competition-promotion\20261021')
AUDIO=OUT/'audio-rate2'
SLIDES=sorted((OUT/'assets').glob('0[1-8]-*.png'))
FF=r'C:\Users\HUAWEI\AppData\Roaming\Python\Python313\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe'
segments=[
'灵感很多，依据很少。科研起步最难的，往往不是想出题目，而是判断它为什么值得做。',
'研证，一款从灵感到课题的 AI 科研工作台，让研究判断有据可循。',
'从问题澄清、文献对照，到候选修订与验证回流，形成一个不断积累证据的闭环。',
'千问帮助澄清、生成与修订；两个公开的 ModelScope Skill，把方向探索和文献证据的职责说清楚。',
'文献命中不等于真正读过。系统分别记录来源、版本、阅读位置、精确摘录和人工判断。',
'模型提出建议，但不会自动替你做决定。只有人工确认后，修订才会进入当前研究。',
'每个候选都有出处，每次修订都有理由，让研究过程可以回看、可以复核。',
'现在在线体验研证，让想法有依据，让下一步能验证。'
]
# durations
voice_files=[AUDIO/f'voice-{i:02d}.wav' for i in range(1,9)]
durs=[]
for p in voice_files:
    with wave.open(str(p),'rb') as w: durs.append(w.getnframes()/w.getframerate())
lead=.18; gap=.28
base=[d+gap for d in durs]; base[0]+=lead
# concatenate voice with initial lead and gaps
params=None; raw=[]
for i,p in enumerate(voice_files):
    with wave.open(str(p),'rb') as w:
        if params is None: params=w.getparams()
        data=w.readframes(w.getnframes())
    if i==0: raw.append(b'\x00\x00'*int(params.framerate*lead))
    raw.append(data)
    raw.append(b'\x00\x00'*int(params.framerate*gap))
with wave.open(str(OUT/'voice-master.wav'),'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(params.framerate); w.writeframes(b''.join(raw))
# ambient score
sr=48000; total=sum(base)+1.0; n=int(sr*total); t=np.arange(n,dtype=np.float64)/sr
pad=(.24*np.sin(2*np.pi*55*t)+.16*np.sin(2*np.pi*82.41*t)+.10*np.sin(2*np.pi*110*t)+.06*np.sin(2*np.pi*164.81*t))
mod=.72+.28*np.sin(2*np.pi*.055*t-.7)**2
# airy shimmer and distant pulse
shimmer=.025*np.sin(2*np.pi*440*t + .45*np.sin(2*np.pi*.09*t))
noise=np.random.default_rng(21).normal(0,1,n)
b,a=butter(2,[90/(sr/2),1400/(sr/2)],btype='band'); air=lfilter(b,a,noise)
air=air/(np.max(np.abs(air))+1e-9)
pulse=np.zeros_like(t)
for sec in [0,8,16,24,32,40,48]:
    mask=t>=sec; local=t[mask]-sec; pulse[mask]+=np.exp(-local*2.7)*np.sin(2*np.pi*46*local)
mix=(pad*mod*.55 + shimmer + .028*air + .055*pulse)
# slow swells aligned with key beats
env=.52+.48*np.sin(2*np.pi*t/18-1.2)**2
mix*=env
# high shelf-like edge fade
fade_in=np.clip(t/2.5,0,1); fade_out=np.clip((total-t)/4.0,0,1); mix*=fade_in*fade_out
mix=mix/(np.max(np.abs(mix))+1e-9)*.8
stereo=np.column_stack([mix,mix*.97+np.roll(mix,13)*.03]).clip(-.8,.8)
pcm=(stereo*32767).astype('<i2').tobytes()
with wave.open(str(OUT/'ambient-score.wav'),'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm)
# ASS subtitles
def ass_time(x):
    h=int(x//3600); m=int((x%3600)//60); s=x%60
    return f'{h}:{m:02d}:{s:05.2f}'
subs=[]; cursor=0; start=lead
for i,(dur,msg) in enumerate(zip(durs,segments),1):
    line=msg
    if len(msg)>34:
        cut=msg.find('；')
        if cut<0: cut=msg.find('。',20)
        if cut<0: cut=len(msg)//2
        line=msg[:cut+1]+r'\N'+msg[cut+1:]
    subs.append((start,start+dur+.12,f'[{i:02d}] {line}'))
    cursor += dur+gap; start=cursor
header='''[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Noto Sans SC,38,&H00FFFFFF,&H00FFFFFF,&H00121B22,&H8C07111A,-1,0,0,0,100,100,0,0,3,1,0,2,70,70,40,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'''
body='\n'.join(f'Dialogue: 0,{ass_time(a)},{ass_time(b)},Default,,0,0,0,,{txt}' for a,b,txt in subs)
(OUT/'subtitles.ass').write_text(header+body+'\n',encoding='utf-8')
# FFmpeg filter graph: subtle crossfades between slides
trans=.35; args=[FF,'-y','-hide_banner']
image_durations=[d+trans for d in base[:-1]]+[base[-1]]
for p,d in zip(SLIDES,image_durations): args += ['-loop','1','-t',f'{d:.3f}','-i',str(p)]
args += ['-i',str(OUT/'voice-master.wav'),'-i',str(OUT/'ambient-score.wav')]
parts=[]
for i,d in enumerate(image_durations): parts.append(f'[{i}:v]scale=1920:1080,setsar=1,trim=duration={d:.3f},setpts=PTS-STARTPTS,fps=30,settb=1/30[v{i}]')
last='v0'; cum=0
for i in range(1,8):
    offset=sum(base[:i])
    out=f'x{i}'
    parts.append(f'[{last}][v{i}]xfade=transition=fade:duration={trans}:offset={offset:.3f}[{out}]')
    last=out
parts.append(f'[{last}]ass=subtitles.ass[vout]')
parts.append('[8:a]aresample=48000,pan=stereo|c0=c0|c1=c0,highpass=f=75,lowpass=f=9000,acompressor=threshold=-20dB:ratio=3:attack=5:release=90,loudnorm=I=-16:LRA=7:TP=-1.5[vo]')
parts.append('[9:a]volume=0.18,afade=t=in:st=0:d=1.2[bed]')
parts.append('[vo][bed]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.92[aout]')
filtergraph=';'.join(parts)
args += ['-filter_complex',filtergraph,'-map','[vout]','-map','[aout]','-t',f'{sum(base)+trans:.3f}','-c:v','libx264','-preset','medium','-crf','20','-profile:v','high','-level','4.1','-pix_fmt','yuv420p','-r','30','-c:a','aac','-b:a','160k','-ar','48000','-movflags','+faststart',str(OUT/'研证-实践宣传-1920x1080.mp4')]
(OUT/'ffmpeg-command.json').write_text(json.dumps({'duration':round(sum(base)+trans,3),'segment_durations':base,'filtergraph':filtergraph},ensure_ascii=False,indent=2),encoding='utf-8')
print('Rendering',round(sum(base)+trans,3),'seconds')
subprocess.run(args,cwd=OUT,check=True)
print(OUT/'研证-实践宣传-1920x1080.mp4')
