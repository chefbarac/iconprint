#!/usr/bin/env node

import WebSocket from 'ws'
import http from 'http'
import * as number from 'lib0/number'
import { setupWSConnection } from '@y/websocket-server/utils'   // ← correct path

const host = process.env.HOST || '0.0.0.0'
const port = number.parseInt(process.env.PORT || '1234')

// Increase the limit (500 MB). Use 0 to disable the limit completely.
const maxPayload = 500 * 1024 * 1024

const wss = new WebSocket.Server({
	noServer: true,
	maxPayload: maxPayload
})

const server = http.createServer((_request, response) => {
	response.writeHead(200, { 'Content-Type': 'text/plain' })
	response.end('okay')
})

wss.on('connection', setupWSConnection)

server.on('upgrade', (request, socket, head) => {
	wss.handleUpgrade(request, socket, head, (ws) => {
		wss.emit('connection', ws, request)
	})
})

server.listen(port, host, () => {
	console.log(`running at '${host}' on port ${port} (maxPayload: ${maxPayload} bytes)`)
})