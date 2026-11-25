/** 对账单类型（F-06，与后端 camelCase 契约对齐）；partyType：1=客户 / 2=供应商。 */

export interface StatementItem {
  bizDate: string
  sourceNo: string | null
  summary: string | null
  debitAmount: string | number
  creditAmount: string | number
  balance: string | number
}

export interface Statement {
  id: number
  statementNo: string
  partyType: number
  partyId: number
  partyName: string | null
  periodStart: string
  periodEnd: string
  beginAmount: string | number
  currentAmount: string | number
  endAmount: string | number
  status: number
  remark: string | null
  createdAt: string | null
  items?: StatementItem[]
}

export interface StatementQuery {
  page: number
  pageSize: number
  partyType: number | null
  partyId: number | null
  periodStart: string | null
  periodEnd: string | null
  startDate: string | null
  endDate: string | null
  statementNo: string | null
}

export interface StatementCreate {
  partyType: 1 | 2
  partyId: number
  periodStart: string
  periodEnd: string
  remark?: string | null
}

export interface StatementBuild extends Omit<Statement, 'id' | 'statementNo' | 'status' | 'createdAt'> {}
