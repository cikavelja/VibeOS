export type ComponentType = 'heading'|'text'|'input'|'button'|'counter';
export interface Component {id:string; type:ComponentType; label:string}
export interface Definition {name:string; components:Component[]}
export interface Project {id:string; definition:Definition}
export type Values = Record<string,string|number>;
export interface Installation extends Project {state:Values}
export interface Share extends Project {accepted:boolean}

export interface HealthResponse {
  status: 'ok';
  service: 'vibeos';
  version?: string;
}

export type HealthState =
  | { kind: 'checking' }
  | { kind: 'healthy'; data: HealthResponse }
  | { kind: 'unhealthy'; error: string };
