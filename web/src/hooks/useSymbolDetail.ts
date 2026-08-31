import { useState, useEffect } from 'react'
import { symbolDetail, symbolFinancials } from '@/api'

/** 标的详情（REST 响应解析结果） */
export interface SymbolDetail {
  symbol: string
  name: string
  industry: string
  region: string
  listing_date: string
  total_shares: number
  float_shares: number
  market_value: number
  flow_market_value: number
}

/** 财务记录（REST 响应解析结果） */
export interface FinancialRecord {
  report_date: string
  announce_date: string
  revenue: number
  net_profit: number
  research_expenses: number
  eps: number
  bps: number
  roe: number
  roe_weighted: number
  gross_margin: number
  net_profit_margin: number
  net_profit_yoy: number
  revenue_yoy: number
  debt_to_assets: number
  ocf: number
  capex: number
  investing_cf: number
  financing_cf: number
  net_cash_change: number
  cash_from_sales: number
}

export function useSymbolDetail(symbol: string | null) {
  const [detail, setDetail] = useState<SymbolDetail | null>(null)
  const [financials, setFinancials] = useState<FinancialRecord[]>([])
  const [loading, setLoading] = useState(false)
  const [loadingFin, setLoadingFin] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [finError, setFinError] = useState<string | null>(null)

  useEffect(() => {
    if (!symbol) {
      setDetail(null)
      setFinancials([])
      return
    }

    const controller = new AbortController()
    setLoading(true)
    setLoadingFin(true)
    setError(null)
    setFinError(null)
    setDetail(null)
    setFinancials([])

    symbolDetail({ path: { symbol }, signal: controller.signal })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return
        if (error || !data) throw new Error('获取详情失败')
        setDetail(parseSymbolDetail(data, symbol))
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted) return
        if (e instanceof DOMException && e.name === 'AbortError') return
        setError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })

    symbolFinancials({ path: { symbol }, signal: controller.signal })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return
        if (error) throw new Error('获取财务数据失败')
        setFinancials(parseFinancialRecords(data?.financials))
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted) return
        if (e instanceof DOMException && e.name === 'AbortError') return
        setFinError(e instanceof Error ? e.message : String(e))
        setFinancials([])
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingFin(false)
      })

    return () => controller.abort()
  }, [symbol])

  return { detail, financials, loading, loadingFin, error, finError }
}

function parseSymbolDetail(
  data: Record<string, unknown>,
  fallbackSymbol: string,
): SymbolDetail {
  return {
    symbol: toString(data.symbol, fallbackSymbol),
    name: toString(data.name),
    industry: toString(data.industry),
    region: toString(data.region),
    listing_date: toString(data.listing_date),
    total_shares: toNumber(data.total_shares),
    float_shares: toNumber(data.float_shares),
    market_value: toNumber(data.market_value),
    flow_market_value: toNumber(data.flow_market_value),
  }
}

function parseFinancialRecords(
  raw: Array<{ [key: string]: unknown }> | undefined,
): FinancialRecord[] {
  if (!raw) return []
  return raw.map((item) => ({
    report_date: toString(item.report_date),
    announce_date: toString(item.announce_date),
    revenue: toNumber(item.revenue),
    net_profit: toNumber(item.net_profit),
    research_expenses: toNumber(item.research_expenses),
    eps: toNumber(item.eps),
    bps: toNumber(item.bps),
    roe: toNumber(item.roe),
    roe_weighted: toNumber(item.roe_weighted),
    gross_margin: toNumber(item.gross_margin),
    net_profit_margin: toNumber(item.net_profit_margin),
    net_profit_yoy: toNumber(item.net_profit_yoy),
    revenue_yoy: toNumber(item.revenue_yoy),
    debt_to_assets: toNumber(item.debt_to_assets),
    ocf: toNumber(item.ocf),
    capex: toNumber(item.capex),
    investing_cf: toNumber(item.investing_cf),
    financing_cf: toNumber(item.financing_cf),
    net_cash_change: toNumber(item.net_cash_change),
    cash_from_sales: toNumber(item.cash_from_sales),
  }))
}

function toString(value: unknown, fallback = ''): string {
  if (value == null) return fallback
  return String(value)
}

function toNumber(value: unknown): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}
