import { useEffect, useCallback } from 'react'
import { X, Building2, Loader2 } from 'lucide-react'
import { useSymbolDetail } from '@/hooks/useSymbolDetail'
import { SymbolFinancialCharts } from '@/components/dashboard/charts/SymbolFinancialCharts'

interface Props {
  symbol: string | null
  onClose: () => void
}

export function SymbolDetailDialog({ symbol, onClose }: Props) {
  const { detail, financials, loading, loadingFin, error, finError } = useSymbolDetail(symbol)

  const handleClose = useCallback(() => onClose(), [onClose])

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [handleClose])

  if (!symbol) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      role="presentation"
      onClick={handleClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="symbol-detail-title"
        className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-lg border border-border bg-card p-0 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-border px-5 py-3">
          <div id="symbol-detail-title" className="flex items-center gap-2">
            <Building2 className="h-4 w-4 text-primary" />
            <span className="text-sm font-semibold">{detail?.name || symbol}</span>
            {detail?.name && (
              <span className="font-mono text-sm text-muted-foreground">{symbol}</span>
            )}
          </div>
          <button
            type="button"
            onClick={handleClose}
            className="text-muted-foreground hover:text-foreground transition-colors"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body — 可滚动 */}
        <div className="flex-1 overflow-y-auto p-5">
          {loading && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-primary" />
            </div>
          )}

          {error && (
            <div className="text-center text-sm text-destructive py-8">
              获取详情失败: {error}
            </div>
          )}

          {detail && !loading && !error && (
            <>
              {/* 公司信息 — 两列网格 */}
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                公司信息
              </h3>
              <div className="mb-6 grid grid-cols-2 gap-x-6 gap-y-2">
                <DetailRow label="公司名称" value={detail.name || '-'} />
                <DetailRow label="所属行业" value={detail.industry || '-'} />
                <DetailRow label="所在地区" value={detail.region || '-'} />
                <DetailRow label="上市日期" value={detail.listing_date || '-'} />
                <DetailRow
                  label="总股本"
                  value={formatShares(detail.total_shares)}
                />
                <DetailRow
                  label="流通股本"
                  value={formatShares(detail.float_shares)}
                />
                <DetailRow
                  label="总市值"
                  value={formatMarketValue(detail.market_value)}
                />
                <DetailRow
                  label="流通市值"
                  value={formatMarketValue(detail.flow_market_value)}
                />
              </div>

              {/* 历年财务数据可视化 */}
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                历年财务数据
              </h3>

              {loadingFin && (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-4 w-4 animate-spin text-primary" />
                </div>
              )}

              {!loadingFin && finError && (
                <div className="py-4 text-center text-sm text-destructive">
                  财务数据加载失败: {finError}
                </div>
              )}

              {!loadingFin && !finError && financials.length === 0 && (
                <div className="py-6 text-center text-sm text-muted-foreground">
                  暂无财务数据
                </div>
              )}

              {!loadingFin && financials.length > 0 && (
                <SymbolFinancialCharts financials={financials} />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-border/50 pb-1.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-sm font-medium text-right">{value}</span>
    </div>
  )
}

function formatShares(shares: number): string {
  if (!shares || shares <= 0) return '-'
  if (shares >= 1e8) return `${(shares / 1e8).toFixed(2)} 亿股`
  if (shares >= 1e4) return `${(shares / 1e4).toFixed(2)} 万股`
  return `${shares.toFixed(0)} 股`
}

function formatMarketValue(value: number): string {
  if (!value || value <= 0) return '-'
  if (value >= 1e8) return `${(value / 1e8).toFixed(2)} 亿元`
  if (value >= 1e4) return `${(value / 1e4).toFixed(2)} 万元`
  return `${value.toFixed(0)} 元`
}
