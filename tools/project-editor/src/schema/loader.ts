import Ajv, { type ValidateFunction } from 'ajv'
import addFormats from 'ajv-formats'

let validator: ValidateFunction | null = null

export async function loadSchema(): Promise<object | null> {
  const r = await fetch('/.pg/skills/src/runtime/spec/project.schema.json')
  if (!r.ok) throw new Error(`Failed to load schema: HTTP ${r.status}`)
  return JSON.parse(await r.text())
}

export function compileValidator(schema: object): ValidateFunction {
  const ajv = new Ajv({ allErrors: true, verbose: true, strict: false })
  addFormats(ajv)
  validator = ajv.compile(schema)
  return validator
}

export type ValidationError = {
  path: string
  message: string
  keyword: string
}

export function validateProject(data: unknown): ValidationError[] {
  if (!validator) return [{ path: '', message: 'Schema not loaded', keyword: 'error' }]
  const valid = validator(data)
  if (valid) return []
  return (validator.errors || []).map(e => ({
    path: e.instancePath || '/',
    message: e.message || 'Unknown error',
    keyword: e.keyword || 'unknown',
  }))
}