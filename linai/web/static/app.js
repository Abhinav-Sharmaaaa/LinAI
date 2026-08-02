/* ==========================================================================
   linai Web UI — Application Logic
   ========================================================================== */

class LinaiApp {
    constructor() {
        this.config = null;
        this.provider = 'openrouter';
        this.models = [];
        this.workflows = [];
        this.logs = [];
        this.autoRefreshLogs = true;
        this.logRefreshInterval = null;
        this.statusRefreshInterval = null;

        this.init();
    }

    async init() {
        this.bindEvents();
        this.loadTheme();
        await this.loadConfig();
        this.updateUI();
        this.startStatusPolling();
        this.loadModels();
        this.loadWorkflows();
        this.loadLogs();
    }

    /* ==========================================================================
       Event Binding
       ========================================================================== */

    bindEvents() {
        // Theme toggle
        document.getElementById('theme-toggle').addEventListener('click', () => this.toggleTheme());

        // Tab navigation
        document.querySelectorAll('.nav-tab').forEach(tab => {
            tab.addEventListener('click', () => this.switchTab(tab.dataset.tab));
        });

        // Provider form toggles
        document.querySelectorAll('.toggle-visibility').forEach(btn => {
            btn.addEventListener('click', () => this.togglePasswordVisibility(btn));
        });

        // Range inputs
        document.getElementById('or-temperature').addEventListener('input', (e) => {
            document.getElementById('or-temp-value').textContent = parseFloat(e.target.value).toFixed(1);
        });
        document.getElementById('nv-temperature').addEventListener('input', (e) => {
            document.getElementById('nv-temp-value').textContent = parseFloat(e.target.value).toFixed(1);
        });

        // Provider badge selection
        document.querySelectorAll('.provider-badge').forEach(badge => {
            badge.addEventListener('click', () => this.selectProvider(badge.dataset.provider));
        });

        // Save config
        document.getElementById('save-config-btn').addEventListener('click', () => this.saveConfig());

        // Export/Import config
        document.getElementById('export-config-btn').addEventListener('click', () => this.exportConfig());
        document.getElementById('import-config-btn').addEventListener('click', () => {
            document.getElementById('import-file').click();
        });
        document.getElementById('import-file').addEventListener('change', (e) => this.importConfig(e));

        // Test connection
        document.getElementById('test-connection-btn').addEventListener('click', () => this.testConnection());

        // Quick actions
        document.querySelectorAll('[data-action]').forEach(btn => {
            btn.addEventListener('click', () => this.handleQuickAction(btn.dataset.action));
        });

        // Model provider filter
        document.getElementById('model-provider-filter').addEventListener('change', () => this.renderModelsTable());

        // Refresh models
        document.getElementById('refresh-models-btn').addEventListener('click', () => this.loadModels());

        // Workflows
        document.getElementById('new-workflow-btn').addEventListener('click', () => this.openWorkflowModal());
        document.getElementById('add-step-btn').addEventListener('click', () => this.addWorkflowStep());
        document.getElementById('workflow-form').addEventListener('submit', (e) => this.saveWorkflow(e));

        // Modal close
        document.querySelectorAll('.modal-close').forEach(btn => {
            btn.addEventListener('click', () => this.closeModals());
        });
        document.querySelectorAll('.modal-overlay').forEach(overlay => {
            overlay.addEventListener('click', () => this.closeModals());
        });

        // Logs
        document.getElementById('refresh-logs-btn').addEventListener('click', () => this.loadLogs());
        document.getElementById('clear-logs-btn').addEventListener('click', () => this.clearLogs());
        document.getElementById('log-level-filter').addEventListener('change', () => this.renderLogs());
        document.getElementById('auto-refresh-logs').addEventListener('change', (e) => {
            this.autoRefreshLogs = e.target.checked;
            this.toggleAutoRefreshLogs();
        });

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => this.handleKeyboardShortcuts(e));
    }

    handleKeyboardShortcuts(e) {
        // Ctrl/Cmd + S to save config
        if ((e.ctrlKey || e.metaKey) && e.key === 's') {
            e.preventDefault();
            if (document.querySelector('.tab-panel.active[data-tab="providers"]')) {
                this.saveConfig();
            }
        }
        // Escape to close modals
        if (e.key === 'Escape') {
            this.closeModals();
        }
        // Number keys for tabs
        if (e.altKey && e.key >= '1' && e.key <= '5') {
            const tabs = ['overview', 'providers', 'workflows', 'models', 'logs'];
            const index = parseInt(e.key) - 1;
            if (tabs[index]) {
                this.switchTab(tabs[index]);
            }
        }
    }

    /* ==========================================================================
       Theme Management
       ========================================================================== */

    loadTheme() {
        const savedTheme = localStorage.getItem('linai-theme') || 'dark';
        document.documentElement.setAttribute('data-theme', savedTheme);
    }

    toggleTheme() {
        const currentTheme = document.documentElement.getAttribute('data-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', newTheme);
        localStorage.setItem('linai-theme', newTheme);
    }

    /* ==========================================================================
       Tab Navigation
       ========================================================================== */

    switchTab(tabName) {
        // Update nav tabs
        document.querySelectorAll('.nav-tab').forEach(tab => {
            const isActive = tab.dataset.tab === tabName;
            tab.classList.toggle('active', isActive);
            tab.setAttribute('aria-selected', isActive);
        });

        // Update tab panels
        document.querySelectorAll('.tab-panel').forEach(panel => {
            panel.classList.toggle('active', panel.dataset.tab === tabName);
        });

        // Load data for specific tabs
        if (tabName === 'models' && this.models.length === 0) {
            this.loadModels();
        }
        if (tabName === 'workflows' && this.workflows.length === 0) {
            this.loadWorkflows();
        }
        if (tabName === 'logs') {
            this.loadLogs();
        }
    }

    /* ==========================================================================
       Config Management
       ========================================================================== */

    async loadConfig() {
        try {
            const response = await fetch('/api/config');
            this.config = await response.json();
            this.provider = this.config.provider || 'openrouter';
            this.updateProviderUI();
        } catch (error) {
            console.error('Failed to load config:', error);
            this.showToast('error', 'Error', 'Failed to load configuration');
        }
    }

    getFormConfig() {
        const provider = document.querySelector('.provider-badge.active')?.dataset.provider || this.provider;

        const config = { provider };

        // OpenRouter fields
        const orKey = document.getElementById('or-api-key').value;
        if (orKey) config.openrouter_key = orKey;

        // NVIDIA fields
        const nvKey = document.getElementById('nv-api-key').value;
        if (nvKey) config.nvidia_key = nvKey;
        const nvUrl = document.getElementById('nv-api-url').value;
        if (nvUrl) config.nvidia_url = nvUrl;

        // Model settings
        const orModel = document.getElementById('or-model').value;
        if (orModel) config.model = orModel;

        const nvModel = document.getElementById('nv-model').value;
        if (nvModel) config.nvidia_model = nvModel;

        config.temperature = parseFloat(document.getElementById(provider === 'nvidia_nim' ? 'nv-temperature' : 'or-temperature').value);
        config.max_tokens = parseInt(document.getElementById(provider === 'nvidia_nim' ? 'nv-max-tokens' : 'or-max-tokens').value);

        return config;
    }

    async saveConfig() {
        const btn = document.getElementById('save-config-btn');
        const originalText = btn.innerHTML;
        btn.innerHTML = '<span class="spinner"></span> Saving...';
        btn.disabled = true;

        try {
            const config = this.getFormConfig();
            const response = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            });
            const data = await response.json();

            if (data.success) {
                this.config = { ...this.config, ...config };
                this.showToast('success', 'Saved', 'Configuration saved successfully');
                await this.loadConfig();
                this.updateUI();
            } else {
                this.showToast('error', 'Error', data.message || 'Failed to save configuration');
            }
        } catch (error) {
            console.error('Save config error:', error);
            this.showToast('error', 'Error', 'Failed to save configuration');
        } finally {
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    }

    updateProviderUI() {
        // Update provider badges
        document.querySelectorAll('.provider-badge').forEach(badge => {
            badge.classList.toggle('active', badge.dataset.provider === this.provider);
        });

        // Show/hide provider forms
        document.getElementById('openrouter-form').style.display = this.provider === 'openrouter' ? 'block' : 'none';
        document.getElementById('nvidia-form').style.display = this.provider === 'nvidia_nim' ? 'block' : 'none';

        // Update form values from config
        if (this.config) {
            if (this.config.openrouter_key) {
                document.getElementById('or-api-key').placeholder = '•••••••• (saved)';
            }
            if (this.config.nvidia_key) {
                document.getElementById('nv-api-key').placeholder = '•••••••• (saved)';
            }
            if (this.config.nvidia_url) {
                document.getElementById('nv-api-url').value = this.config.nvidia_url;
            }
            if (this.config.model) {
                document.getElementById('or-model').value = this.config.model;
            }
            if (this.config.nvidia_model) {
                document.getElementById('nv-model').value = this.config.nvidia_model;
            }
            if (this.config.temperature) {
                const temp = this.config.temperature;
                document.getElementById('or-temperature').value = temp;
                document.getElementById('or-temp-value').textContent = temp.toFixed(1);
                document.getElementById('nv-temperature').value = temp;
                document.getElementById('nv-temp-value').textContent = temp.toFixed(1);
            }
            if (this.config.max_tokens) {
                document.getElementById('or-max-tokens').value = this.config.max_tokens;
                document.getElementById('nv-max-tokens').value = this.config.max_tokens;
            }
        }

        this.updateModelsForProvider();
    }

    selectProvider(provider) {
        this.provider = provider;
        this.updateProviderUI();
    }

    updateModelsForProvider() {
        // This will be populated after models are loaded
    }

    /* ==========================================================================
       Model Loading
       ========================================================================== */

    async loadModels() {
        const btn = document.getElementById('refresh-models-btn');
        if (btn) {
            btn.innerHTML = '<span class="spinner"></span> Refreshing...';
            btn.disabled = true;
        }

        try {
            const response = await fetch('/api/models');
            const data = await response.json();
            this.models = data.models || [];
            this.renderModelsTable();
            this.populateModelSelects();
        } catch (error) {
            console.error('Failed to load models:', error);
            this.showToast('error', 'Error', 'Failed to load models');
        } finally {
            if (btn) {
                btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg> Refresh';
                btn.disabled = false;
            }
        }
    }

    populateModelSelects() {
        const orModels = this.models.filter(m => m.provider === 'openrouter' || !m.provider);
        const nvModels = this.models.filter(m => m.provider === 'nvidia_nim');

        const orSelect = document.getElementById('or-model');
        const nvSelect = document.getElementById('nv-model');

        // Preserve current selection
        const orCurrent = orSelect.value;
        const nvCurrent = nvSelect.value;

        orSelect.innerHTML = '<option value="">Select a model...</option>';
        orModels.forEach(model => {
            const option = document.createElement('option');
            option.value = model.id;
            option.textContent = `${model.id} ${model.context_length ? `(${this.formatContextLength(model.context_length)})` : ''}`;
            option.dataset.pricing = JSON.stringify(model.pricing);
            orSelect.appendChild(option);
        });
        if (orCurrent) orSelect.value = orCurrent;

        nvSelect.innerHTML = '<option value="">Select a model...</option>';
        nvModels.forEach(model => {
            const option = document.createElement('option');
            option.value = model.id;
            option.textContent = `${model.id} ${model.context_length ? `(${this.formatContextLength(model.context_length)})` : ''}`;
            option.dataset.pricing = JSON.stringify(model.pricing);
            nvSelect.appendChild(option);
        });
        if (nvCurrent) nvSelect.value = nvCurrent;
    }

    renderModelsTable() {
        const tbody = document.getElementById('models-tbody');
        const filter = document.getElementById('model-provider-filter').value;

        let filteredModels = this.models;
        if (filter !== 'all') {
            filteredModels = this.models.filter(m => m.provider === filter);
        }

        if (filteredModels.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="loading-cell">No models found</td></tr>';
            return;
        }

        tbody.innerHTML = filteredModels.map(model => `
            <tr>
                <td><code class="model-id">${this.escapeHtml(model.id)}</code></td>
                <td><span class="provider-tag ${model.provider || 'unknown'}">${model.provider || 'Unknown'}</span></td>
                <td>${model.context_length ? this.formatContextLength(model.context_length) : 'N/A'}</td>
                <td class="pricing">${this.formatPricing(model.pricing)}</td>
                <td>
                    <div class="table-actions">
                        <button class="icon-btn" title="Select for ${model.provider === 'nvidia_nim' ? 'NVIDIA' : 'OpenRouter'}" data-model="${this.escapeHtml(model.id)}" data-provider="${model.provider}">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                        </button>
                    </div>
                </td>
            </tr>
        `).join('');

        // Add click handlers for model selection
        tbody.querySelectorAll('[data-model]').forEach(btn => {
            btn.addEventListener('click', () => this.selectModel(btn.dataset.model, btn.dataset.provider));
        });
    }

    selectModel(modelId, provider) {
        if (provider === 'nvidia_nim') {
            document.getElementById('nv-model').value = modelId;
        } else {
            document.getElementById('or-model').value = modelId;
        }
        this.showToast('info', 'Model Selected', `${modelId} selected for ${provider === 'nvidia_nim' ? 'NVIDIA NIM' : 'OpenRouter'}`);
    }

    /* ==========================================================================
       Workflows
       ========================================================================== */

    async loadWorkflows() {
        try {
            const response = await fetch('/api/workflows');
            const data = await response.json();
            this.workflows = data.workflows || [];
            this.renderWorkflows();
        } catch (error) {
            console.error('Failed to load workflows:', error);
        }
    }

    renderWorkflows() {
        const container = document.getElementById('workflow-list');

        if (this.workflows.length === 0) {
            container.innerHTML = `
                <div class="workflow-empty">
                    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="margin-bottom: 16px; color: var(--text-muted);">
                        <path d="M9 11l3 3L22 4"/>
                        <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7"/>
                    </svg>
                    <h3>No workflows yet</h3>
                    <p>Create your first workflow to automate tasks</p>
                    <button class="btn btn-primary" style="margin-top: 16px;" onclick="app.openWorkflowModal()">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                        Create Workflow
                    </button>
                </div>
            `;
            return;
        }

        container.innerHTML = this.workflows.map(wf => `
            <div class="workflow-item" data-id="${wf.id}">
                <div class="workflow-info">
                    <div class="workflow-name">${this.escapeHtml(wf.name || 'Untitled')}</div>
                    <div class="workflow-description">${this.escapeHtml(wf.description || 'No description')}</div>
                    <div class="workflow-meta">
                        <span>${wf.steps ? wf.steps.length : 0} steps</span>
                        <span>Created ${this.formatDate(wf.created_at)}</span>
                    </div>
                </div>
                <div class="workflow-actions">
                    <button class="btn btn-secondary btn-sm" onclick="app.executeWorkflow('${wf.id}')">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                        Run
                    </button>
                    <button class="btn btn-secondary btn-sm" onclick="app.editWorkflow('${wf.id}')">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                    </button>
                    <button class="btn btn-danger btn-sm" onclick="app.deleteWorkflow('${wf.id}')">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                </div>
            </div>
        `).join('');
    }

    openWorkflowModal(workflow = null) {
        const modal = document.getElementById('workflow-modal');
        const title = document.getElementById('workflow-modal-title');
        const form = document.getElementById('workflow-form');
        const stepsContainer = document.getElementById('workflow-steps');

        form.reset();
        stepsContainer.innerHTML = '';

        if (workflow) {
            title.textContent = 'Edit Workflow';
            document.getElementById('workflow-id').value = workflow.id;
            document.getElementById('wf-name').value = workflow.name || '';
            document.getElementById('wf-description').value = workflow.description || '';

            (workflow.steps || []).forEach((step, index) => {
                this.addWorkflowStep(step);
            });
        } else {
            title.textContent = 'Create Workflow';
            document.getElementById('workflow-id').value = '';
            this.addWorkflowStep();
        }

        modal.classList.add('active');
        document.getElementById('wf-name').focus();
    }

    addWorkflowStep(value = '') {
        const container = document.getElementById('workflow-steps');
        const stepCount = container.children.length + 1;

        const div = document.createElement('div');
        div.className = 'step-item';
        div.innerHTML = `
            <input type="text" class="step-input" placeholder="Step ${stepCount}: e.g., Analyze codebase for bugs" value="${this.escapeHtml(value)}" required>
            <button type="button" class="icon-btn step-remove" aria-label="Remove step">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        `;

        div.querySelector('.step-remove').addEventListener('click', () => {
            if (container.children.length > 1) {
                div.remove();
                this.updateStepPlaceholders();
            } else {
                this.showToast('warning', 'Cannot Remove', 'At least one step is required');
            }
        });

        container.appendChild(div);
    }

    updateStepPlaceholders() {
        document.querySelectorAll('.step-input').forEach((input, index) => {
            input.placeholder = `Step ${index + 1}: e.g., Analyze codebase for bugs`;
        });
    }

    async saveWorkflow(e) {
        e.preventDefault();

        const id = document.getElementById('workflow-id').value;
        const name = document.getElementById('wf-name').value.trim();
        const description = document.getElementById('wf-description').value.trim();
        const steps = Array.from(document.querySelectorAll('.step-input')).map(input => input.value.trim()).filter(s => s);

        if (!name) {
            this.showToast('error', 'Error', 'Workflow name is required');
            return;
        }

        if (steps.length === 0) {
            this.showToast('error', 'Error', 'At least one step is required');
            return;
        }

        const workflow = { name, description, steps };

        try {
            const response = await fetch('/api/workflows', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(workflow)
            });
            const data = await response.json();

            if (data.success) {
                this.showToast('success', 'Saved', id ? 'Workflow updated' : 'Workflow created');
                this.closeModals();
                this.loadWorkflows();
            } else {
                this.showToast('error', 'Error', 'Failed to save workflow');
            }
        } catch (error) {
            console.error('Save workflow error:', error);
            this.showToast('error', 'Error', 'Failed to save workflow');
        }
    }

    async executeWorkflow(workflowId) {
        this.showToast('info', 'Running', 'Executing workflow...');

        try {
            const response = await fetch('/api/workflows/execute', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ workflow_id: workflowId })
            });
            const data = await response.json();

            if (data.success) {
                this.showToast('success', 'Complete', 'Workflow executed successfully');
            } else {
                this.showToast('error', 'Error', data.result || 'Workflow execution failed');
            }
        } catch (error) {
            console.error('Execute workflow error:', error);
            this.showToast('error', 'Error', 'Failed to execute workflow');
        }
    }

    editWorkflow(workflowId) {
        const workflow = this.workflows.find(w => w.id === workflowId);
        if (workflow) {
            this.openWorkflowModal(workflow);
        }
    }

    async deleteWorkflow(workflowId) {
        if (!confirm('Are you sure you want to delete this workflow?')) return;

        // Note: The backend doesn't have a delete endpoint yet, but we can update the workflow list
        // For now, we'll just show a message
        this.showToast('info', 'Info', 'Delete functionality requires backend support');
    }

    /* ==========================================================================
       Connection Testing
       ========================================================================== */

    async testConnection() {
        const btn = document.getElementById('test-connection-btn');
        const originalText = btn.innerHTML;
        btn.innerHTML = '<span class="spinner"></span> Testing...';
        btn.disabled = true;

        const provider = this.provider;
        const config = {
            provider,
            model: provider === 'nvidia_nim' ? document.getElementById('nv-model').value : document.getElementById('or-model').value,
        };

        if (provider === 'openrouter') {
            const key = document.getElementById('or-api-key').value;
            if (key) config.openrouter_key = key;
        } else {
            const key = document.getElementById('nv-api-key').value;
            if (key) config.nvidia_key = key;
            const url = document.getElementById('nv-api-url').value;
            if (url) config.nvidia_url = url;
        }

        try {
            const response = await fetch('/api/test-connection', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            });
            const data = await response.json();

            if (data.success) {
                this.showToast('success', 'Connected', data.response || 'Connection successful!');
                this.updateConnectionStatus('connected');
            } else {
                this.showToast('error', 'Failed', data.error || 'Connection failed');
                this.updateConnectionStatus('error');
            }
        } catch (error) {
            console.error('Test connection error:', error);
            this.showToast('error', 'Error', 'Failed to test connection');
            this.updateConnectionStatus('error');
        } finally {
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    }

    updateConnectionStatus(status) {
        const indicator = document.getElementById('connection-status');
        indicator.className = 'status-indicator ' + status;
        const texts = {
            connected: 'Connected',
            connecting: 'Connecting...',
            error: 'Connection Failed',
            checking: 'Checking...'
        };
        indicator.querySelector('.status-text').textContent = texts[status] || 'Unknown';
    }

    /* ==========================================================================
       System Status Polling
       ========================================================================== */

    startStatusPolling() {
        this.refreshStatus();
        this.statusRefreshInterval = setInterval(() => this.refreshStatus(), 10000);
    }

    async refreshStatus() {
        try {
            const response = await fetch('/api/status');
            const data = await response.json();

            this.updateSystemStats(data.system, data.disk);
            this.updateProviderInfo(data.provider);
            this.updateConnectionStatus(data.api_key_configured ? 'connected' : 'error');
        } catch (error) {
            console.error('Status refresh error:', error);
            this.updateConnectionStatus('error');
        }
    }

    updateSystemStats(system, disk) {
        if (system) {
            const cpu = system.cpu_percent || 0;
            const memory = system.memory_percent || 0;

            document.getElementById('stat-cpu').textContent = `${cpu.toFixed(1)}%`;
            document.getElementById('cpu-percent').textContent = `${cpu.toFixed(1)}%`;
            document.getElementById('cpu-bar').style.width = `${Math.min(cpu, 100)}%`;

            document.getElementById('stat-memory').textContent = `${memory.toFixed(1)}%`;
            document.getElementById('memory-percent').textContent = `${memory.toFixed(1)}%`;
            document.getElementById('memory-bar').style.width = `${Math.min(memory, 100)}%`;
        }

        if (disk) {
            const diskPercent = disk.percent || 0;
            document.getElementById('stat-disk').textContent = `${diskPercent.toFixed(1)}%`;
            document.getElementById('disk-percent').textContent = `${diskPercent.toFixed(1)}%`;
            document.getElementById('disk-bar').style.width = `${Math.min(diskPercent, 100)}%`;
        }
    }

    updateProviderInfo(providerInfo) {
        if (!providerInfo) return;

        document.getElementById('current-provider').textContent = this.formatProviderName(providerInfo.provider);
        document.getElementById('current-model').textContent = providerInfo.default_model || 'Not set';
        document.getElementById('api-key-status').textContent = providerInfo.key_configured ? 'Configured ✓' : 'Not configured';
        document.getElementById('api-key-status').style.color = providerInfo.key_configured ? 'var(--accent-success)' : 'var(--accent-warning)';
        document.getElementById('api-endpoint').textContent = providerInfo.api_url || 'N/A';

        const badge = document.getElementById('provider-badge');
        badge.textContent = this.formatProviderName(providerInfo.provider);
        badge.className = 'badge ' + (providerInfo.key_configured ? 'badge-success' : 'badge-warning');
    }

    /* ==========================================================================
       Logs
       ========================================================================== */

    async loadLogs() {
        try {
            const response = await fetch('/api/logs');
            const data = await response.json();
            this.logs = data.logs || [];
            this.renderLogs();
        } catch (error) {
            console.error('Failed to load logs:', error);
        }
    }

    renderLogs() {
        const container = document.getElementById('logs-content');
        const filter = document.getElementById('log-level-filter').value;

        let filteredLogs = this.logs;
        if (filter !== 'all') {
            filteredLogs = this.logs.filter(log => log.level === filter);
        }

        if (filteredLogs.length === 0) {
            container.innerHTML = '<div class="loading">No logs found</div>';
            return;
        }

        // Show last 200 logs
        const displayLogs = filteredLogs.slice(-200);

        container.innerHTML = displayLogs.map(log => `
            <div class="log-line">
                <span class="log-time">${this.escapeHtml(log.time || '')}</span>
                <span class="log-level ${log.level || 'info'}">${(log.level || 'info').toUpperCase()}</span>
                <span class="log-message">${this.escapeHtml(log.message || '')}</span>
            </div>
        `).join('');

        // Auto-scroll to bottom
        container.scrollTop = container.scrollHeight;
    }

    clearLogs() {
        this.logs = [];
        this.renderLogs();
        this.showToast('info', 'Cleared', 'Logs cleared');
    }

    toggleAutoRefreshLogs() {
        if (this.autoRefreshLogs) {
            this.logRefreshInterval = setInterval(() => this.loadLogs(), 5000);
        } else {
            clearInterval(this.logRefreshInterval);
        }
    }

    /* ==========================================================================
       Quick Actions
       ========================================================================== */

    handleQuickAction(action) {
        switch (action) {
            case 'new-workflow':
                this.switchTab('workflows');
                setTimeout(() => this.openWorkflowModal(), 100);
                break;
            case 'refresh-models':
                this.loadModels();
                break;
            case 'view-logs':
                this.switchTab('logs');
                break;
            case 'export-config':
                this.exportConfig();
                break;
        }
    }

    /* ==========================================================================
       Config Export/Import
       ========================================================================== */

    exportConfig() {
        const config = {
            ...this.config,
            exportedAt: new Date().toISOString(),
            version: '1.0'
        };

        // Remove sensitive data for export
        delete config.openrouter_key;
        delete config.nvidia_key;

        const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `linai-config-${new Date().toISOString().split('T')[0]}.json`;
        a.click();
        URL.revokeObjectURL(url);

        this.showToast('success', 'Exported', 'Configuration exported (keys omitted for security)');
    }

    importConfig(event) {
        const file = event.target.files[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = async (e) => {
            try {
                const config = JSON.parse(e.target.result);
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(config)
                });
                const data = await response.json();

                if (data.success) {
                    this.showToast('success', 'Imported', 'Configuration imported successfully');
                    await this.loadConfig();
                    this.updateUI();
                } else {
                    this.showToast('error', 'Error', 'Failed to import configuration');
                }
            } catch (error) {
                console.error('Import error:', error);
                this.showToast('error', 'Error', 'Invalid configuration file');
            }
            event.target.value = '';
        };
        reader.readAsText(file);
    }

    /* ==========================================================================
       UI Updates
       ========================================================================== */

    updateUI() {
        this.updateProviderUI();
        this.renderModelsTable();
        this.renderWorkflows();
        this.renderLogs();
    }

    /* ==========================================================================
       Toast Notifications
       ========================================================================== */

    showToast(type, title, message) {
        const container = document.getElementById('toast-container');
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;

        const icons = {
            success: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
            error: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
            warning: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
            info: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'
        };

        toast.innerHTML = `
            <div class="toast-icon">${icons[type]}</div>
            <div class="toast-content">
                <div class="toast-title">${this.escapeHtml(title)}</div>
                <div class="toast-message">${this.escapeHtml(message)}</div>
            </div>
            <button class="toast-close" aria-label="Dismiss">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        `;

        toast.querySelector('.toast-close').addEventListener('click', () => this.removeToast(toast));
        container.appendChild(toast);

        // Auto-remove after 5 seconds
        setTimeout(() => this.removeToast(toast), 5000);
    }

    removeToast(toast) {
        toast.classList.add('removing');
        setTimeout(() => toast.remove(), 200);
    }

    /* ==========================================================================
       Password Visibility Toggle
       ========================================================================== */

    togglePasswordVisibility(button) {
        const targetId = button.dataset.target;
        const input = document.getElementById(targetId);
        const isPassword = input.type === 'password';
        input.type = isPassword ? 'text' : 'password';
        button.classList.toggle('active', !isPassword);
    }

    /* ==========================================================================
       Modal Management
       ========================================================================== */

    closeModals() {
        document.querySelectorAll('.modal').forEach(modal => modal.classList.remove('active'));
    }

    /* ==========================================================================
       Utility Functions
       ========================================================================== */

    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    formatProviderName(provider) {
        const names = {
            'openrouter': 'OpenRouter',
            'nvidia_nim': 'NVIDIA NIM',
            'nvidia': 'NVIDIA NIM'
        };
        return names[provider] || provider;
    }

    formatContextLength(length) {
        if (length >= 1000000) return `${(length / 1000000).toFixed(1)}M`;
        if (length >= 1000) return `${(length / 1000).toFixed(0)}k`;
        return length.toString();
    }

    formatPricing(pricing) {
        if (!pricing) return 'N/A';
        const input = pricing.prompt || pricing.input || 0;
        const output = pricing.completion || pricing.output || 0;
        if (input === 0 && output === 0) return 'Free';
        return `$${input.toFixed(4)} / $${output.toFixed(4)} per 1k tokens`;
    }

    formatDate(timestamp) {
        if (!timestamp) return 'Unknown';
        const date = new Date(timestamp * 1000);
        return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    }

    /* ==========================================================================
       Cleanup
       ========================================================================== */

    destroy() {
        clearInterval(this.statusRefreshInterval);
        clearInterval(this.logRefreshInterval);
    }
}

// Initialize app when DOM is ready
let app;
document.addEventListener('DOMContentLoaded', () => {
    app = new LinaiApp();
});

// Make app globally accessible for inline handlers
window.app = app;