// Build the browser client: compile TypeScript to dist/js, copy the page and style sheet next to it.
// Uses the project's TypeScript if installed (npm install), otherwise a `tsc` on the PATH.
import { execFileSync } from 'node:child_process';
import { cpSync, mkdirSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
const args = ['-p', join(root, 'tsconfig.build.json')];
rmSync(join(dist, 'js'), { recursive: true, force: true });
mkdirSync(dist, { recursive: true });
let tsc = null;
try { tsc = createRequire(join(root, 'package.json')).resolve('typescript/bin/tsc'); } catch { /* not installed locally */ }
if (tsc) execFileSync(process.execPath, [tsc, ...args], { stdio: 'inherit', cwd: root });
else execFileSync(process.platform === 'win32' ? 'tsc.cmd' : 'tsc', args, { stdio: 'inherit', cwd: root, shell: process.platform === 'win32' });
cpSync(join(root, 'src', 'client', 'index.html'), join(dist, 'index.html'));
cpSync(join(root, 'src', 'client', 'styles.css'), join(dist, 'styles.css'));
console.log('Built dist/');
