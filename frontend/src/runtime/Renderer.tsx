import {useState} from 'react';
import {increment} from './state';
import type {Definition, Values} from '../types';
export function Renderer({definition,values,onChange}:{definition:Definition;values:Values;onChange:(values:Values)=>void}){
 const [notice,setNotice]=useState('');
 return <div className="runtime">{definition.components.map(c=>{
 switch(c.type){
 case 'heading': return <h2 key={c.id}>{c.label}</h2>;
 case 'text': return <p key={c.id}>{c.label}</p>;
 case 'input': return <label key={c.id}>{c.label}<input maxLength={2000} value={String(values[c.id]??'')} onChange={e=>onChange({...values,[c.id]:e.target.value})}/></label>;
 case 'counter': return <button key={c.id} onClick={()=>onChange(increment(values,c.id))}>{c.label}: {values[c.id]??0}</button>;
 case 'button': return <button key={c.id} onClick={()=>setNotice(`${c.label} clicked`)}>{c.label}</button>;
 }})}<p role="status">{notice}</p></div>;
}
