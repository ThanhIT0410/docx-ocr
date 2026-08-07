// Waits for `nuxt dev` (started in parallel by the `electron:dev` script) to
// be reachable, then launches Electron pointed at it. Keeps local dev to a
// single command without hand-timing two processes.
import { spawn } from 'node:child_process'
import waitOn from 'wait-on'
import electronPath from 'electron'

await waitOn({ resources: ['http://localhost:3000'], timeout: 60_000 })

const child = spawn(electronPath, ['.'], { stdio: 'inherit' })
child.on('exit', (code) => process.exit(code ?? 0))
