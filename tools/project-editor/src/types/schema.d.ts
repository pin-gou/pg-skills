export type JSONSchema7 = {
  type?: string | string[]
  enum?: unknown[]
  const?: unknown
  minimum?: number
  maximum?: number
  minLength?: number
  maxLength?: number
  pattern?: string
  format?: string
  default?: unknown
  description?: string
  multiline?: boolean
  properties?: Record<string, JSONSchema7>
  required?: string[]
  additionalProperties?: boolean | JSONSchema7
  items?: JSONSchema7 | JSONSchema7[]
  oneOf?: JSONSchema7[]
  anyOf?: JSONSchema7[]
  allOf?: JSONSchema7[]
  minItems?: number
  maxItems?: number
  $ref?: string
  [k: string]: unknown
}