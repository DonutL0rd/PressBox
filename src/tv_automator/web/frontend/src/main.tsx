import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './index.css'
import { TvAutomatorProvider } from './hooks/useTvAutomator'

console.log("[PressBox] Frontend Build: " + new Date().toISOString());

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <TvAutomatorProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </TvAutomatorProvider>
  </React.StrictMode>
)
