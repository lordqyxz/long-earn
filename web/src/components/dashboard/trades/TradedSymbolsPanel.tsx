import { Info } from 'lucide-react'
import { SymbolChart } from '@/components/dashboard/charts/SymbolChart'
import type { SymbolChartData } from '@/api'

interface Props {
  tradedSymbols: string[]
  selectedSymbol: string | null
  symbolNames: Record<string, string>
  symbolData: SymbolChartData | null
  symbolLoading: boolean
  onSelect: (symbol: string) => void
  onOpenDetail: (symbol: string) => void
}

export function TradedSymbolsPanel({
  tradedSymbols,
  selectedSymbol,
  symbolNames,
  symbolData,
  symbolLoading,
  onSelect,
  onOpenDetail,
}: Props) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
      <div className="lg:col-span-1 max-h-[320px] overflow-auto">
        {tradedSymbols.length === 0 ? (
          <div className="text-center text-muted-foreground py-4 text-sm">暂无交易标的</div>
        ) : (
          <div className="space-y-0.5">
            {tradedSymbols.map((sym) => {
              const name = symbolNames[sym]
              const isSelected = selectedSymbol === sym
              return (
                <div
                  key={sym}
                  className={`group flex items-center gap-1 px-2 py-2 rounded-md transition-colors ${
                    isSelected
                      ? 'bg-primary text-primary-foreground font-medium'
                      : 'hover:bg-muted text-muted-foreground'
                  }`}
                >
                  <button
                    onClick={() => onSelect(sym)}
                    className="flex-1 text-left"
                  >
                    <div className="text-sm truncate">{name || sym}</div>
                    {name && (
                      <div className={`font-mono text-xs truncate ${isSelected ? 'text-primary-foreground/70' : 'text-muted-foreground/70'}`}>
                        {sym}
                      </div>
                    )}
                  </button>
                  <button
                    onClick={() => onOpenDetail(sym)}
                    className={`shrink-0 p-1 rounded transition-opacity ${
                      isSelected
                        ? 'text-primary-foreground/60 hover:text-primary-foreground'
                        : 'text-muted-foreground/40 hover:text-muted-foreground'
                    }`}
                    title="查看公司信息"
                  >
                    <Info className="h-3.5 w-3.5" />
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="lg:col-span-3">
        <SymbolChart
          data={symbolData}
          loading={symbolLoading}
          symbolName={selectedSymbol ? symbolNames[selectedSymbol] : undefined}
        />
      </div>
    </div>
  )
}
