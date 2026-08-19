#!/usr/bin/env node

import http from 'http'
import WebSocket from 'ws'                    // default import
import * as number from 'lib0/number'
import { setupWSConnection } from 'y-websocket/bin/utils'

const host = process.env.HOST || '0.0.0.0'
const port = number.parseInt(process.env.PORT || '1234')
const maxPayload = 500 * 1024 * 1024          // 500 MB

const wss = new WebSocket.Server({            // WebSocket.Server (not WebSocketServer)
	noServer: true,
	maxPayload
})

const server = http.createServer((_req, res) => {
	res.writeHead(200, { 'Content-Type': 'text/plain' })
	res.end('okay')
})

wss.on('connection', setupWSConnection)

server.on('upgrade', (request, socket, head) => {
	wss.handleUpgrade(request, socket, head, (ws) => {
		wss.emit('connection', ws, request)
	})
})

server.listen(port, host, () => {
	console.log(`Yjs 13 server running at ${host}:${port} (maxPayload: ${maxPayload} bytes)`)
})