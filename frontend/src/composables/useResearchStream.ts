import { ref } from 'vue'
import type { ResearchEvent } from '../types'

export function useResearchStream() {
  const isRunning = ref(false)
  const error = ref<string | null>(null)
  let controller: AbortController | undefined

  async function start(topic: string, onEvent: (event: ResearchEvent) => void) {
    controller?.abort()
    controller = new AbortController()
    isRunning.value = true
    error.value = null

    try {
      const response = await fetch('/api/research/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ topic }),
        signal: controller.signal,
      })
      if (!response.ok || !response.body) {
        throw new Error(`无法建立研究流：HTTP ${response.status}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
        const packets = buffer.split('\n\n')
        buffer = packets.pop() || ''
        for (const packet of packets) {
          const eventLine = packet.split('\n').find((line) => line.startsWith('event:'))
          const dataLine = packet.split('\n').find((line) => line.startsWith('data:'))
          if (!eventLine || !dataLine) continue
          try {
            const event = JSON.parse(dataLine.slice(5).trim()) as ResearchEvent
            onEvent(event)
          } catch {
            // Ignore malformed keep-alive packets rather than breaking the UI.
          }
        }
        if (done) break
      }
    } catch (caught) {
      if ((caught as Error).name !== 'AbortError') {
        error.value = caught instanceof Error ? caught.message : '研究连接失败'
      }
    } finally {
      isRunning.value = false
    }
  }

  function cancel() {
    controller?.abort()
    controller = undefined
    isRunning.value = false
  }

  return { isRunning, error, start, cancel }
}
