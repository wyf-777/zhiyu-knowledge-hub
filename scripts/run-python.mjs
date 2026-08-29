import { spawnSync } from 'node:child_process';

const forwarded = process.argv.slice(2);
const candidates = process.platform === 'win32'
  ? [['python', []], ['py', ['-3']]]
  : [['python3', []], ['python', []]];

for (const [command, prefix] of candidates) {
  const environment = { ...process.env, PYTHONUTF8: '1' };
  const probe = spawnSync(command, [...prefix, '--version'], { stdio: 'ignore', env: environment });
  if (probe.status !== 0) continue;
  const result = spawnSync(command, [...prefix, ...forwarded], { stdio: 'inherit', env: environment });
  process.exit(result.status ?? 1);
}

console.error('未找到 Python 3。请先安装 Python 3.11 或更高版本。');
process.exit(1);
