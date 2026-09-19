// t4: count EXECUTABLE innerHTML assignments in all .js under static/.
// Comment- and string-aware: uses a real lexer, so a commented-out assignment
// and the same shape inside a string literal do NOT count.
import fs from 'node:fs';
import path from 'node:path';

const DIR = 'services/webui/src/joy_interaction_webui/static';

// Tokenize into a list of {type,value} where type ∈ code|comment|string|regex; keep source positions.
function lex(src) {
  const toks = [];
  let i = 0, prevSig = '';
  const n = src.length;
  while (i < n) {
    const c = src[i], c2 = src[i + 1];
    if (c === '/' && c2 === '/') { const s = i; while (i < n && src[i] !== '\n') i++; toks.push({ type: 'comment', value: src.slice(s, i), pos: s }); continue; }
    if (c === '/' && c2 === '*') { const s = i; i += 2; while (i < n && !(src[i] === '*' && src[i + 1] === '/')) i++; i += 2; toks.push({ type: 'comment', value: src.slice(s, i), pos: s }); continue; }
    if (c === '"' || c === "'" || c === '`') {
      const q = c, s = i; i++;
      while (i < n) { if (src[i] === '\\') { i += 2; continue; } if (src[i] === q) { i++; break; } i++; }
      toks.push({ type: 'string', value: src.slice(s, i), pos: s }); prevSig = q; continue;
    }
    if (c === '/' && /[=(,:[!&|?{};+\-*%~^<>]/.test(prevSig)) {
      const s = i; i++;
      while (i < n) { if (src[i] === '\\') { i += 2; continue; } if (src[i] === '/') { i++; break; } if (src[i] === '\n') break; i++; }
      toks.push({ type: 'regex', value: src.slice(s, i), pos: s }); prevSig = '/'; continue;
    }
    const s = i;
    while (i < n && !/["'`/]/.test(src[i])) { if (!/\s/.test(src[i])) prevSig = src[i]; i++; }
    if (i > s) toks.push({ type: 'code', value: src.slice(s, i), pos: s });
    else { if (!/\s/.test(c)) prevSig = c; i++; toks.push({ type: 'code', value: c, pos: s }); }
  }
  return toks;
}

const files = fs.readdirSync(DIR).filter((f) => f.endsWith('.js')).sort();
let totalExec = 0, totalComment = 0;
const perFile = [];
for (const f of files) {
  const src = fs.readFileSync(path.join(DIR, f), 'utf8');
  const toks = lex(src);
  let exec = 0, comment = 0;
  const hits = [];
  for (const t of toks) {
    // assignment shape: `.innerHTML` then `=` (not == / === / =>)
    const re = /\.innerHTML\s*=(?!=)/g;
    let m;
    while ((m = re.exec(t.value))) {
      if (t.type === 'code') {
        exec++;
        const line = src.slice(0, t.pos + m.index).split('\n').length;
        hits.push(`      L${line}: ${JSON.stringify(src.split('\n')[line - 1].trim().slice(0, 110))}`);
      } else if (t.type === 'comment') comment++;
    }
  }
  totalExec += exec; totalComment += comment;
  perFile.push({ f, exec, comment });
  console.log(`${exec === 0 ? '  ✅' : '  ❌'} ${f.padEnd(26)} executable innerHTML=${exec}  (in comments=${comment})`);
  hits.forEach((h) => console.log(h));
}
console.log(`\n文件数=${files.length}  可执行 innerHTML 赋值总数=${totalExec}  注释中出现的=${totalComment}`);
console.log(totalExec === 0 ? '\n✅ 全部 .js 内可执行 innerHTML 赋值 = 0' : `\n❌ 存在 ${totalExec} 处可执行 innerHTML`);
process.exit(totalExec === 0 ? 0 : 1);
