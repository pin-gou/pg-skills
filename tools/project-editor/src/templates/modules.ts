export type ModuleTemplate = {
  language: string
  root: string
  build?: string
  lint?: string
  test?: Record<string, string>
  review_level?: string
  description?: string
}

export const TEMPLATES: Record<string, ModuleTemplate> = {
  'java-maven': {
    language: 'java',
    root: 'webvirt-newmodule',
    build: 'cd webvirt-newmodule && mvn clean install -DskipTests',
    lint: 'cd webvirt-newmodule && mvn checkstyle:check',
    test: {
      unit: 'cd webvirt-newmodule && mvn test',
    },
    review_level: 'standard',
  },
  'go-make': {
    language: 'go',
    root: 'webvirt-newmodule',
    build: 'cd webvirt-newmodule && make build',
    lint: 'cd webvirt-newmodule && go vet ./...',
    test: {
      unit: 'cd webvirt-newmodule && go test ./...',
    },
    review_level: 'standard',
  },
  'ts-vite': {
    language: 'typescript',
    root: 'webvirt-newmodule',
    build: 'cd webvirt-newmodule && pnpm build',
    lint: 'cd webvirt-newmodule && pnpm lint',
    test: {
      unit: 'cd webvirt-newmodule && pnpm test',
    },
    review_level: 'standard',
  },
  'shell': {
    language: 'shell',
    root: '.pg/hooks',
    build: 'cd .pg/hooks && bash -n *.sh',
    lint: 'cd .pg/hooks && shellcheck -e SC2086,SC2154 *.sh',
    test: {
      unit: 'cd .pg/hooks && for f in fixtures/*.sql; do psql --dry-run -f "$f" || exit 1; done',
    },
    review_level: 'standard',
  },
  'proto': {
    language: 'proto',
    root: 'webvirt-proto',
    build: 'cd webvirt-proto && make proto',
    review_level: 'standard',
  },
}

export function listTemplates(): Array<{ id: string; label: string }> {
  return [
    { id: 'java-maven', label: 'Java + Maven' },
    { id: 'go-make', label: 'Go + Make' },
    { id: 'ts-vite', label: 'TypeScript + Vite + pnpm' },
    { id: 'shell', label: 'Shell 脚本' },
    { id: 'proto', label: 'Proto (生成代码)' },
  ]
}

export function getTemplate(id: string): ModuleTemplate | null {
  return TEMPLATES[id] ?? null
}