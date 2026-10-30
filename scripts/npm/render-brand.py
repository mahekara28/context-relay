"""Generate editable vector README artwork from bundled, attributed harness logos."""
from pathlib import Path
from html import escape
import re
root=Path(__file__).resolve().parents[2]
out=root/'docs/assets/workspace'
font='-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif'
def text(x,y,value,size=18,fill='#232c28',weight=400):
 return f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" fill="{fill}" font-weight="{weight}">{escape(value)}</text>'
def rect(x,y,w,h,fill='#fff',stroke='#dfe4dc',r=14):return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}"/>'
def logo(name,x,y,size=30):
 svg=(root/f'packages/relay/ui/assets/{name}.svg').read_text();svg=re.sub(r'<svg\b[^>]*>',lambda m:re.sub(r'\s(?:width|height)="[^"]*"','',m.group()).replace('<svg',f'<svg x="{x}" y="{y}" width="{size}" height="{size}"'),svg,count=1);return svg
hero=['<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="430" viewBox="0 0 1200 430" role="img" aria-label="Context Relay. Keep the work. Change the thread.">',rect(1,1,1198,428,'#f5f5f0','#dfe4dc',18),logo('relay',50,38,36),text(99,63,'CONTEXT RELAY',17,weight=650),text(50,149,'Keep the work.',55,weight=650),text(50,211,'Change the thread.',55,weight=650),text(53,260,'Reviewed context for your next coding session.',22,'#69746d'),rect(52,306,464,63,'#242b28','#242b28',10),text(73,344,'$ relay demo && relay ui',23,'#f0f4ed'),text(53,399,'LOCAL WORKSPACE  /  CLI  /  MCP',12,'#69746d',650)]
for i,(name,label) in enumerate([('claude','Claude Code'),('codex','Codex'),('opencode','OpenCode'),('goose','Goose')]):
 x=690+(i%2)*214;y=92+(i//2)*132;hero +=[rect(x,y,188,106),logo(name,x+22,y+20,30),text(x+22,y+80,label,19,weight=550)]
hero += [text(695,399,'CAPTURE → REVIEW → CONTINUE',12,'#69746d',650),'</svg>'];(out/'hero.svg').write_text(''.join(hero))
parts=['<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="730" viewBox="0 0 1200 730" role="img" aria-label="Architecture: harness capture adapters and MCP access meet in one local context service with SQLite and optional Nebius proposals.">',rect(1,1,1198,728,'#f5f5f0','#dfe4dc',18),text(44,49,'ONE WORKSPACE. TWO INTEGRATION PATHS.',15,'#69746d',650)]
for i,(name,label) in enumerate([('claude','Claude Code'),('codex','Codex'),('opencode','OpenCode'),('goose','Goose')]):
 x=44+i*282;parts +=[rect(x,80,266,98),logo(name,x+24,109,34),text(x+77,133,label,21,weight=550)]
parts += [text(165,222,'↓ READ NATIVE HISTORY',13,'#69746d',650),text(802,222,'↑ SERVE CONTEXT',13,'#69746d',650),rect(44,244,540,106),rect(616,244,540,106),text(70,282,'Capture adapters',23,weight=600),text(70,318,'JSONL · app-server · local API · JSON exports',17,'#69746d'),text(642,282,'MCP tools + resources',23,weight=600),text(642,318,'Inspect sources · retrieve packet · accept handoff',17,'#69746d'),'<path d="M314 350v30h572v-30M600 380v27" fill="none" stroke="#aab5ac" stroke-width="2"/>',rect(44,408,1112,125,'#fbede7','#c84429'),logo('relay',74,445,45),text(137,458,'Context Relay · local service',27,weight=650),text(137,496,'Capture → Review → Immutable checkpoint → Recipient-bound handoff',20,'#7a4f42'),'<path d="M600 533v30M314 583v-20h572v20" fill="none" stroke="#aab5ac" stroke-width="2"/>',rect(44,584,540,106),rect(616,584,540,106),text(70,623,'Private SQLite state',22,weight=600),text(70,658,'Sources · revisions · file hashes · delivery receipts',16,'#69746d'),text(642,623,'Optional Nebius Token Factory',22,weight=600),text(642,658,'Selected evidence → validated draft → human review',16,'#69746d'),'</svg>'];(out/'architecture.svg').write_text(''.join(parts))
