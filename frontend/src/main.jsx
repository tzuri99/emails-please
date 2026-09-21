import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'

// Theme and CssBaseline live in App: it owns the light/dark toggle.
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
