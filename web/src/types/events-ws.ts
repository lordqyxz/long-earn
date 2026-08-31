/**
 * REST 领域类型由 @hey-api/openapi-ts 从后端 OpenAPI 自动生成（见 src/api/）。
 * 本文件仅保留 OpenAPI 覆盖不到的 /ws/events 消息类型。
 */

/** /ws/events 事件流 WebSocket 消息。 */
export interface PipelineMessage {
  type: 'pipeline_start' | 'pipeline_progress' | 'pipeline_complete' | 'pipeline_error' | 'pong' | 'subscribed'
  query?: string
  stage?: string
  progress?: number
  status?: string
  detail?: string
  stats?: Record<string, unknown>
  message?: string
}
