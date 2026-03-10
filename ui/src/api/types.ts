export interface Task {
  id: string
  title: string
  status: string
  project_id: string
  project_name?: string
  refinement_count?: number
  depends_on?: string[]
  unmet_dependencies?: string[]
}

export interface Project {
  id: string
  name: string
  repo_url: string
  local_path: string
  status: string
}

export interface Spec {
  id: string
  task_id: string
  previous_spec_id: string | null
  content: string
  created_at: string
}

export interface Execution {
  id: string
  task_id: string
  spec_id: string
  status: string
  failure_reason: string | null
  branch_name: string | null
  started_at: string
  completed_at: string | null
}

export interface Feature {
  id: string
  title: string
  description: string
  project_id: string
  project_name?: string
  compiled_spec_count?: number
  total_spec_count?: number
}

export interface BoardGroup {
  status: string
  label: string
  tasks: Task[]
}

export interface BoardResponse {
  groups: BoardGroup[]
}

export interface ProjectDetailResponse {
  project: Project
  tasks: Task[]
}

export interface TaskDetailResponse {
  task: Task
  specs: Spec[]
  executions: Execution[]
  dependencies: string[]
  qa_failure: string | null
}

export interface FeaturesResponse {
  features: Feature[]
}
