import fs from 'node:fs';
import path from 'node:path';

/** 本地运行记录；每次写入先写临时文件再重命名，避免半份 JSON。 */
export class Store {
  constructor(root) {
    this.root = root;
    fs.mkdirSync(path.join(root, 'runs'), { recursive: true, mode: 0o700 });
    fs.chmodSync(root, 0o700);
  }
  read(name, fallback = null) {
    try { return JSON.parse(fs.readFileSync(path.join(this.root, `${name}.json`), 'utf8')); }
    catch (error) { if (error.code === 'ENOENT') return fallback; throw error; }
  }
  write(name, value) {
    if (!/^(runs\/[a-zA-Z0-9-]+|experts|verification)$/.test(name)) throw new Error('Invalid record name');
    const dest = path.join(this.root, `${name}.json`);
    const temp = `${dest}.tmp`;
    fs.writeFileSync(temp, JSON.stringify(value, null, 2), { mode: 0o600 });
    fs.renameSync(temp, dest);
  }
  runs() {
    return fs.readdirSync(path.join(this.root, 'runs'))
      .filter(name => /^[a-zA-Z0-9-]+\.json$/.test(name))
      .map(name => this.read(`runs/${name.slice(0, -5)}`))
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }
  recoverInterrupted() {
    for (const run of this.runs()) if (run.status === 'running') {
      run.status = 'failed';
      run.phase = 'interrupted';
      run.error = '平台上次运行中断。已有观点已保留；请重新发起研究。';
      run.updatedAt = new Date().toISOString();
      this.write(`runs/${run.id}`, run);
    }
  }
}
