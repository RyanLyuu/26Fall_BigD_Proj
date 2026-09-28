import React from 'react';
import { createRoot } from 'react-dom/client';
import 'antd/dist/reset.css';
import 'maplibre-gl/dist/maplibre-gl.css';
import './styles.css';
import App from './App';
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
