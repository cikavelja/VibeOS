import {useState} from 'react';
import type {Definition, ComponentType, Values} from '../types';
import {Renderer} from '../runtime/Renderer';
const kinds:ComponentType[]=['heading','text','input','button','counter'];
export function Studio({onSave}:{onSave:(definition:Definition)=>Promise<void>}){
 const [definition,setDefinition]=useState<Definition>({name:'My application',components:[]});
 const [values,setValues]=useState<Values>({});
 function add(type:ComponentType){setDefinition(d=>({...d,components:[...d.components,{id:crypto.randomUUID(),type,label:type}]}));}
 return <section><h2>Vibe Studio</h2><label>Application name<input value={definition.name} maxLength={100} onChange={e=>setDefinition(d=>({...d,name:e.target.value}))}/></label><div className="palette">{kinds.map(k=><button key={k} onClick={()=>add(k)}>+ {k}</button>)}</div><div className="studio"><div><h3>Components</h3>{definition.components.map((c,i)=><div className="component" key={c.id}><label>{c.type}<input value={c.label} maxLength={100} onChange={e=>setDefinition(d=>({...d,components:d.components.map(x=>x.id===c.id?{...x,label:e.target.value}:x)}))}/></label><button onClick={()=>setDefinition(d=>({...d,components:d.components.filter((_,j)=>j!==i)}))}>Remove</button></div>)}</div><div><h3>Live preview</h3><Renderer definition={definition} values={values} onChange={setValues}/></div></div><button disabled={!definition.name.trim()||!definition.components.length||definition.components.some(c=>!c.label.trim())} onClick={()=>void onSave(definition)}>Save application</button></section>;
}
