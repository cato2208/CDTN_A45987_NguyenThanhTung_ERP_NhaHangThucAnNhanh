/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState, useRef, onMounted, onWillUnmount, onPatched } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const EMPTY_ENTRY = () => ({ product_id: null, product_name: '', qty: 1, reason: 'other', note: '', search: '' });

let chartJsPromise = null;

// Chart.js loader - waits until Chart.js is really available before rendering.
async function _loadChartJS() {
    if (window.Chart) return true;
    if (chartJsPromise) return chartJsPromise;

    const paths = [
        '/web/static/lib/Chart/Chart.js',
        '/web/static/lib/chartjs/chart.umd.js',
        '/web/static/lib/chart.js/chart.umd.js',
    ];

    const loadScript = (src) => new Promise((resolve, reject) => {
        const existing = document.querySelector(`script[src="${src}"]`);
        if (existing) {
            if (window.Chart) {
                resolve(true);
            } else {
                existing.addEventListener('load', () => resolve(!!window.Chart), { once: true });
                existing.addEventListener('error', reject, { once: true });
            }
            return;
        }

        const script = document.createElement('script');
        script.src = src;
        script.async = false;
        script.onload = () => resolve(!!window.Chart);
        script.onerror = () => reject(new Error(`Cannot load Chart.js from ${src}`));
        document.head.appendChild(script);
    });

    chartJsPromise = (async () => {
        for (const path of paths) {
            try {
                if (await loadScript(path)) return true;
            } catch (e) {
                console.warn('[KDSDashboard] Chart.js load failed:', path, e);
            }
        }
        console.warn('[KDSDashboard] Chart.js is not available');
        return false;
    })();

    return chartJsPromise;
}

class KDSDashboard extends Component {
    static template = "mcd_kds.KDSDashboard";

    setup() {
        this.rpc = useService("rpc");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            view: 'manager',
            loading: false,
            error: null,
            data: null,
            wasteTab: 'entry',
            wasteEntries: [EMPTY_ENTRY()],
            wasteReport: null,
            wasteSuccess: null,
            wasteError: null,
            wasteDate: this.todayDate(),
            submitting: false,
            currentUser: (window.odoo && odoo.session_info && odoo.session_info.name) || 'Nhân viên',
            // inventory products for waste dropdown
            inventoryProducts: [],
            dropdownOpen: {},   // { index: true/false }
            // product search/autocomplete
            productSearchQuery: '',
            productSearchOpen: false,
            productSearchResults: [],
            productSearchLoading: false,
            productSearchHasMore: false,
            productSearchLimit: 80,
            salesMetric: 'revenue',
            salesChartType: 'bar',
            salesSort: 'desc',
            period: 'day',
            dashboardDate: this.todayDate(),
            dashboardMonth: null,
            dashboardYear: null,
            // product detail
            selectedProduct: null,
            chartSelection: null,
            selectedReorderProductId: null,
            purchaseCreatingProductId: null,
        });
        this._charts = {};
        this._chartToken = null;

        this.revChart         = useRef('revChart');
        this.wasteReasonChart = useRef('wasteReasonChart');
        this.salesMonthlyChart = useRef('salesMonthlyChart');
        this.salesProductChart = useRef('salesProductChart');
        this.productTrendChart = useRef('productTrendChart');
        this.productHourlyChart = useRef('productHourlyChart');
        this.inventoryIngredientChart = useRef('inventoryIngredientChart');

        onMounted(async () => {
            try {
                console.log('[KDSDashboard] Component mounted, loading Chart.js...');
                await _loadChartJS();
                console.log('[KDSDashboard] Chart.js loaded:', !!window.Chart);
                await this._loadInventoryProducts();
                await this._loadData();
                // Re-render charts on window resize
                this._resizeHandler = () => {
                    console.log('[KDSDashboard] Window resized, updating charts...');
                    Object.values(this._charts).forEach(c => { try { c.resize(); } catch(_) {} });
                };
                window.addEventListener('resize', this._resizeHandler);
                // Global error catch to surface to UI while this component is mounted
                this._globalErrorHandler = (ev) => {
                    try {
                        const msg = ev && ev.message ? ev.message : String(ev || 'Unknown error');
                        console.error('[KDSDashboard] Global error captured:', msg);
                        this.state.error = 'Lỗi: ' + msg;
                    } catch (_) {}
                };
                window.addEventListener('error', this._globalErrorHandler);
            } catch (e) {
                console.error('[KDSDashboard] Error during mounted lifecycle:', e);
                try { this.state.error = 'Không hiển thị được dashboard: ' + (e && e.message ? e.message : e); } catch(_){}
            }
        });
        onPatched(() => {
            console.log('[KDSDashboard] Component patched, syncing charts...');
            this._syncCharts();
        });
        onWillUnmount(() => {
            // Clean up event listeners and charts
            if (this._resizeHandler) {
                window.removeEventListener('resize', this._resizeHandler);
            }
            if (this._globalErrorHandler) {
                window.removeEventListener('error', this._globalErrorHandler);
            }
            if (this._productSearchTimer) {
                clearTimeout(this._productSearchTimer);
            }
            this._destroyCharts();
        });
    }

    //  helpers 
    formatVND(val) {
        const num = Number(val || 0);
        if (!Number.isFinite(num) || !num) return '0 VND';
        if (num >= 1_000_000) {
            const value = num / 1_000_000;
            return value.toLocaleString('vi-VN', {
                minimumFractionDigits: 0,
                maximumFractionDigits: value >= 10 ? 1 : 2,
            }) + ' tr VND';
        }
        if (num >= 1_000) {
            return Math.round(num / 1000).toLocaleString('vi-VN') + 'k VND';
        }
        return Math.round(num).toLocaleString('vi-VN') + ' VND';
    }

    formatTrend(pct) {
        if (pct === null || pct === undefined) {
            return '';
        }
        const sign = pct > 0 ? '+' : '';
        return `${sign}${pct}%`;
    }

    comparisonTone(value) {
        const num = Number(value || 0);
        if (num > 0) return 'up';
        if (num < 0) return 'down';
        return 'flat';
    }

    formatDelta(value, type = 'number') {
        if (value === null || value === undefined) return '';
        const num = Number(value || 0);
        const sign = num > 0 ? '+' : num < 0 ? '-' : '';
        const abs = Math.abs(num);
        if (type === 'money') return `${sign}${this.formatVND(abs)}`;
        if (type === 'percent') return `${sign}${abs}%`;
        if (type === 'qty') return `${sign}${this.formatQty(abs)} món`;
        if (type === 'wasteQty') return `${sign}${this.formatWasteQty(abs)} đv`;
        if (type === 'orders') return `${sign}${this.formatCount(abs)} đơn`;
        return `${sign}${abs}`;
    }

    formatDuration(secs) {
        if (!secs) return '';
        secs = Math.round(secs);
        const m = Math.floor(secs / 60);
        const s = secs % 60;
        return m > 0 ? `${m}p ${s}s` : `${s}s`;
    }

    formatPercent(val) {
        const num = Number(val || 0);
        return `${num.toFixed(num % 1 ? 1 : 0)}%`;
    }

    formatQty(val) {
        const num = Number(val || 0);
        if (!Number.isFinite(num)) return '0';
        return num.toLocaleString('vi-VN', {
            minimumFractionDigits: 0,
            maximumFractionDigits: num % 1 ? 1 : 0,
        });
    }

    formatCount(val) {
        const num = Number(val || 0);
        if (!Number.isFinite(num)) return '0';
        return Math.round(num).toLocaleString('vi-VN');
    }

    formatWasteQty(val) {
        const num = Number(val || 0);
        if (!Number.isFinite(num)) return '0';
        return Math.round(num).toLocaleString('vi-VN');
    }

    formatMeasure(val, uom = 'đơn vị') {
        const num = Number(val || 0);
        if (!Number.isFinite(num)) return `0 ${uom}`;
        const decimals = Math.abs(num) < 10 && num % 1 ? 1 : 0;
        return num.toLocaleString('vi-VN', {
            minimumFractionDigits: 0,
            maximumFractionDigits: decimals,
        }) + ' ' + (uom || 'đơn vị');
    }

    healthTone(score) {
        const value = Number(score || 0);
        if (value >= 85) return 'good';
        if (value >= 70) return 'warn';
        return 'bad';
    }

    todayDate() {
        const now = new Date();
        return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    }

    currentMonth() {
        const now = new Date();
        return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    }

    currentYear() {
        return String(new Date().getFullYear());
    }

    isFutureDate(dateValue) {
        return !!dateValue && dateValue > this.todayDate();
    }

    periodLabel() {
        if (this.state.dashboardDate) return this.state.dashboardDate;
        if (this.state.dashboardMonth) return this.state.dashboardMonth;
        if (this.state.dashboardYear) return this.state.dashboardYear;
        if (this.state.period === 'week') return 'Tuần này';
        if (this.state.period === 'month') return 'Tháng này';
        if (this.state.period === 'year') return 'Năm nay';
        return 'Hôm nay';
    }

    selectProductFromSearch(product) {
        this.state.productSearchQuery = product.name || '';
        this.state.productSearchOpen = false;
        this.state.chartSelection = null;
        this._destroyCharts();
        // Load product detail when selected
        this._loadProductDetail(product);
    }

    closeProductDetail() {
        this.state.selectedProduct = null;
        this.state.chartSelection = null;
        this._destroyCharts();
    }

    async _loadProductDetail(product) {
        try {
            this.state.loading = true;
            const productId = product.id || product.key;
            if (!productId) {
                this.state.error = 'Không tìm thấy ID sản phẩm';
                return;
            }
            const result = await this.rpc('/mcd_kds/product_detail', {
                ...this._periodPayload(),
                product_id: productId,
            });
            if (result && result.error) {
                this.state.error = result.error;
                this.state.selectedProduct = null;
                return;
            }
            this.state.selectedProduct = result;
            this.state.error = null;
            this.state.chartSelection = null;
            setTimeout(() => this._syncCharts(), 0);
        } catch (e) {
            console.error('[ProductDetail] Error loading:', e);
            this.state.error = 'Lỗi tải chi tiết sản phẩm: ' + (e && e.message ? e.message : e);
        } finally {
            this.state.loading = false;
        }
    }

    salesProductOptions() {
        const items = new Map();
        const add = (product) => {
            if (!product || !product.name) return;
            const key = String(product.key || product.id || product.product_id || product.name);
            if (!items.has(key)) {
                items.set(key, {
                    key,
                    id: product.id || product.product_id || (product.key && Number(product.key)) || null,
                    name: product.name,
                    revenue: product.revenue,
                    qty: product.qty,
                    order_count: product.order_count,
                });
            }
        };
        (this.state.data?.top_products || []).forEach(add);
        (this.state.productSearchResults || []).forEach(add);
        (this.state.data?.daily_products || []).forEach((day) => {
            (day.products || []).forEach(add);
        });
        return Array.from(items.values());
    }

    filteredProducts(searchText) {
        const q = (searchText || '').toLowerCase().trim();
        const items = (this.state.productSearchResults || []).length
            ? this.state.productSearchResults
            : this.salesProductOptions();
        if (!q) return items;
        return items.filter((p) => p.name.toLowerCase().includes(q));
    }

    salesMetricLabel(metric = this.state.salesMetric) {
        const labels = {
            revenue: 'Tổng giá',
            qty: 'Số lượng',
            order_count: 'Số đơn',
        };
        return labels[metric] || labels.revenue;
    }

    salesMetricUnit(metric = this.state.salesMetric) {
        const units = {
            revenue: 'tr VND',
            qty: 'món',
            order_count: 'đơn',
        };
        return units[metric] || '';
    }

    setSalesMetric(metric) {
        this.state.salesMetric = metric;
        this.state.chartSelection = null;
        this._destroyCharts();
        setTimeout(() => this._syncCharts(), 0);
    }

    setSalesChartType(type) {
        this.state.salesChartType = type;
        this.state.chartSelection = null;
        this._destroyCharts();
        setTimeout(() => this._syncCharts(), 0);
    }

    toggleSalesSort() {
        this.state.salesSort = this.state.salesSort === 'desc' ? 'asc' : 'desc';
        this.state.chartSelection = null;
        this._destroyCharts();
        setTimeout(() => this._syncCharts(), 0);
    }

    salesProductChartData() {
        const metric = this.state.salesMetric || 'revenue';
        const products = this.salesSortedProducts().slice(0, 12);
        const values = products.map((p) => {
            const value = Number(p?.[metric] || 0);
            return metric === 'revenue' ? Number((value / 1_000_000).toFixed(2)) : Number(value.toFixed ? value.toFixed(2) : value);
        });
        return {
            products,
            labels: products.map((p) => p.name),
            values,
            label: this.salesMetricLabel(metric),
            color: metric === 'revenue' ? '#4EA6E8' : (metric === 'qty' ? '#27AE60' : '#8E44AD'),
        };
    }

    salesSortedProducts() {
        const metric = this.state.salesMetric || 'revenue';
        const rows = [...(this.state.data?.top_products || [])];
        rows.sort((a, b) => {
            const av = Number(a?.[metric] || 0);
            const bv = Number(b?.[metric] || 0);
            return this.state.salesSort === 'asc' ? av - bv : bv - av;
        });
        return rows;
    }

    salesMonthlyChartData() {
        const metric = this.state.salesMetric || 'revenue';
        const chart = this.state.data?.monthly_chart || {};
        const labels = chart.labels || [];
        let values = chart.revenue_values || chart.values || [];
        if (metric === 'qty') {
            values = chart.qty_values || [];
        } else if (metric === 'order_count') {
            values = chart.order_values || [];
        }
        return {
            labels,
            values: values.map((v) => Number(v || 0)),
            label: metric === 'revenue' ? 'Doanh thu (triệu VND)' : this.salesMetricLabel(metric),
            color: metric === 'revenue' ? '#27AE60' : (metric === 'qty' ? '#F2C94C' : '#8E44AD'),
        };
    }

    salesMetricTotal(metric = this.state.salesMetric) {
        if (metric === 'qty') {
            return Number(this.state.data?.total_items || 0);
        }
        if (metric === 'order_count') {
            return Number(this.state.data?.total_orders || 0);
        }
        return Number(this.state.data?.total_revenue || 0);
    }

    salesCompositionSummary() {
        const metric = this.state.salesMetric || 'revenue';
        const rows = [...(this.state.data?.top_products || [])].sort((a, b) => {
            return Number(b?.[metric] || 0) - Number(a?.[metric] || 0);
        });
        const total = this.salesMetricTotal(metric);
        const topProduct = rows[0] || {};
        const topValue = Number(topProduct?.[metric] || 0);
        const top3Value = rows.slice(0, 3).reduce((sum, item) => sum + Number(item?.[metric] || 0), 0);
        return {
            topProduct: topProduct.name || '—',
            topProductShare: total ? Number((topValue / total * 100).toFixed(1)) : 0,
            top3Share: total ? Number((top3Value / total * 100).toFixed(1)) : 0,
            metricLabel: this.salesMetricLabel(metric).toLowerCase(),
        };
    }

    salesProductShare(product, metric = this.state.salesMetric) {
        const total = this.salesMetricTotal(metric);
        const value = Number(product?.[metric] || 0);
        return total ? Number((value / total * 100).toFixed(1)) : 0;
    }

    salesProductMetricValue(product, metric = this.state.salesMetric) {
        const value = Number(product?.[metric] || 0);
        if (metric === 'revenue') {
            return this.formatVND(value);
        }
        if (metric === 'qty') {
            return `${this.formatQty(value)} món`;
        }
        return `${this.formatCount(value)} đơn`;
    }

    salesMetricComparisonKey(metric = this.state.salesMetric) {
        if (metric === 'qty') {
            return 'items';
        }
        if (metric === 'order_count') {
            return 'orders';
        }
        return 'revenue';
    }

    salesMetricDeltaType(metric = this.state.salesMetric) {
        if (metric === 'qty') {
            return 'qty';
        }
        if (metric === 'order_count') {
            return 'orders';
        }
        return 'money';
    }

    salesMetricComparison(metric = this.state.salesMetric) {
        const key = this.salesMetricComparisonKey(metric);
        return this.state.data?.comparison?.[key] || {};
    }

    clearChartSelection() {
        this.state.chartSelection = null;
    }

    async _loadSalesProductSearch(query = this.state.productSearchQuery, limit = this.state.productSearchLimit) {
        this.state.productSearchLoading = true;
        try {
            const result = await this.rpc('/mcd_kds/products/search', {
                query: query || '',
                limit,
            });
            this.state.productSearchResults = result?.products || [];
            this.state.productSearchHasMore = !!result?.has_more;
        } catch (e) {
            console.warn('[KDSDashboard] Cannot search products:', e);
            this.state.productSearchResults = [];
            this.state.productSearchHasMore = false;
        } finally {
            this.state.productSearchLoading = false;
        }
    }

    _normalizeDateInput(value) {
        const clean = (value || '').trim();
        return /^\d{4}-\d{2}-\d{2}$/.test(clean) ? clean : null;
    }

    //  inventory products 
    async _loadInventoryProducts() {
        try {
            const products = await this.rpc('/mcd_kds/inventory_products', {});
            this.state.inventoryProducts = products || [];
        } catch(e) {
            this.state.inventoryProducts = [];
        }
    }

    filteredInventoryProducts(searchText) {
        const q = (searchText || '').toLowerCase().trim();
        if (!q) return this.state.inventoryProducts;
        return this.state.inventoryProducts.filter(p =>
            p.name.toLowerCase().includes(q)
        );
    }

    // Product search handlers
    openProductSearch() {
        this.state.productSearchOpen = true;
        if (!(this.state.productSearchResults || []).length) {
            this._loadSalesProductSearch();
        }
    }
    closeProductSearch() {
        setTimeout(() => { this.state.productSearchOpen = false; }, 200);
    }
    onProductSearchInput(value) {
        this.state.productSearchQuery = value || '';
        this.state.productSearchOpen = true;
        this.state.productSearchLimit = 80;
        if (this._productSearchTimer) {
            clearTimeout(this._productSearchTimer);
        }
        this._productSearchTimer = setTimeout(() => {
            this._loadSalesProductSearch(this.state.productSearchQuery, this.state.productSearchLimit);
        }, 120);
    }

    loadMoreProductSearch() {
        this.state.productSearchLimit += 80;
        this.state.productSearchOpen = true;
        this._loadSalesProductSearch(this.state.productSearchQuery, this.state.productSearchLimit);
    }

    //  navigation 
    setView(view) {
        this._destroyCharts();
        this.state.view         = view;
        this.state.data         = null;
        this.state.error        = null;
        this.state.chartSelection = null;
        this.state.wasteSuccess = null;
        this.state.wasteError   = null;
        this._loadData();
    }

    setPeriod(period) {
        this._destroyCharts();
        this.state.period = period;
        this.state.dashboardDate = null;
        this.state.dashboardMonth = null;
        this.state.dashboardYear = null;
        this.state.chartSelection = null;
        this.state.selectedProduct = null;
        this._loadData();
    }

    setDashboardDate(dateValue) {
        let cleanDate = this._normalizeDateInput(dateValue);
        if (this.isFutureDate(cleanDate)) {
            cleanDate = this.todayDate();
        }
        this.state.period = 'day';
        this.state.dashboardDate = cleanDate;
        this.state.wasteDate = cleanDate;
        this.state.dashboardMonth = null;
        this.state.dashboardYear = null;
        this.state.chartSelection = null;
        this.state.selectedProduct = null;
        this._destroyCharts();
        this._loadData();
    }

    setDashboardMonth(monthValue) {
        const cleanMonth = (monthValue || '').slice(0, 7);
        this.state.period = 'month';
        this.state.dashboardDate = null;
        this.state.dashboardMonth = cleanMonth || null;
        this.state.dashboardYear = null;
        this.state.chartSelection = null;
        this.state.selectedProduct = null;
        this._destroyCharts();
        this._loadData();
    }

    setDashboardYear(yearValue) {
        const year = String(yearValue || '').replace(/\D/g, '').slice(0, 4);
        this.state.period = 'year';
        this.state.dashboardDate = null;
        this.state.dashboardMonth = null;
        this.state.dashboardYear = year || null;
        this.state.chartSelection = null;
        this.state.selectedProduct = null;
        this._destroyCharts();
        this._loadData();
    }

    setWasteDate(dateValue) {
        let cleanDate = this._normalizeDateInput(dateValue);
        if (this.isFutureDate(cleanDate)) {
            cleanDate = this.todayDate();
        }
        this.state.wasteDate = cleanDate || this.todayDate();
        this.state.wasteSuccess = null;
        this.state.wasteError = null;
    }

    _periodPayload() {
        const payload = { period: this.state.period };
        if (this.state.period === 'day' && this.state.dashboardDate) {
            payload.selected_date = this.state.dashboardDate;
        }
        if (this.state.period === 'month' && this.state.dashboardMonth) {
            payload.selected_month = this.state.dashboardMonth;
        }
        if (this.state.period === 'year' && this.state.dashboardYear) {
            payload.selected_year = this.state.dashboardYear;
        }
        return payload;
    }

    setWasteTab(tab) {
        this._destroyCharts();
        this.state.wasteTab = tab;
        if (tab === 'report') this._loadWasteReport();
    }

    toggleDemo() {
        this.state.useDemo = !this.state.useDemo;
        localStorage.setItem('kds_demo_mode', this.state.useDemo ? 'true' : 'false');
        this._destroyCharts();
        this.state.data = null;
        this._loadData();
    }

    //  data loading 
    _normalizeDashboardData(view, data) {
        const normalized = data || {};
        if (view === 'manager') {
            normalized.top_products = normalized.top_products || [];
            normalized.chart_revenue = normalized.chart_revenue || { labels: [], values: [] };
            normalized.alerts = normalized.alerts || [];
            normalized.health_score = normalized.health_score || 0;
            normalized.health_status = normalized.health_status || '';
            normalized.kitchen_sla_rate = normalized.kitchen_sla_rate ?? 100;
            normalized.expo_sla_rate = normalized.expo_sla_rate ?? 100;
            normalized.waste_ratio = normalized.waste_ratio || 0;
            normalized.waste_loss = normalized.waste_loss || 0;
            normalized.kitchen_overdue_count = normalized.kitchen_overdue_count || 0;
            normalized.expo_overdue_count = normalized.expo_overdue_count || 0;
            normalized.low_stock_count = normalized.low_stock_count || 0;
            normalized.comparison = normalized.comparison || {};
            normalized.composition = normalized.composition || {};
            normalized.profitability = normalized.profitability || {};
            normalized.customer_mix = normalized.customer_mix || {};
            normalized.action_alerts = normalized.action_alerts || [];
            normalized.statistical_insights = normalized.statistical_insights || [];
        } else if (view === 'kitchen') {
            normalized.overdue_orders = normalized.overdue_orders || [];
            normalized.peak_hours = normalized.peak_hours || [];
            normalized.top_items = normalized.top_items || [];
            normalized.slow_products = normalized.slow_products || [];
            normalized.aging_buckets = normalized.aging_buckets || [];
            normalized.duration_buckets = normalized.duration_buckets || [];
            normalized.insights = normalized.insights || [];
            normalized.sla_rate = normalized.sla_rate ?? 100;
            normalized.late_done_count = normalized.late_done_count || 0;
            normalized.overdue_count = normalized.overdue_count || 0;
            normalized.waiting_count = normalized.waiting_count || 0;
            normalized.eat_in = normalized.eat_in || 0;
            normalized.take_out = normalized.take_out || 0;
        } else if (view === 'expo') {
            normalized.overdue_orders = normalized.overdue_orders || [];
            normalized.peak_hours = normalized.peak_hours || [];
            normalized.top_items = normalized.top_items || [];
            normalized.slow_products = normalized.slow_products || [];
            normalized.aging_buckets = normalized.aging_buckets || [];
            normalized.duration_buckets = normalized.duration_buckets || [];
            normalized.insights = normalized.insights || [];
            normalized.sla_rate = normalized.sla_rate ?? 100;
            normalized.late_done_count = normalized.late_done_count || 0;
            normalized.avg_handoff_delay = normalized.avg_handoff_delay || 0;
            normalized.overdue_count = normalized.overdue_count || 0;
            normalized.waiting_count = normalized.waiting_count || 0;
            normalized.eat_in = normalized.eat_in || 0;
            normalized.take_out = normalized.take_out || 0;
            normalized.total_done = normalized.total_done || 0;
        } else if (view === 'sales') {
            normalized.top_products = normalized.top_products || [];
            normalized.daily_products = normalized.daily_products || [];
            normalized.monthly_chart = normalized.monthly_chart || { labels: [], values: [] };
            normalized.product_chart = normalized.product_chart || { labels: [], values: [] };
            normalized.total_revenue = normalized.total_revenue || 0;
            normalized.total_orders = normalized.total_orders || 0;
            normalized.avg_order_value = normalized.avg_order_value || 0;
            normalized.total_items = normalized.total_items || 0;
            normalized.comparison = normalized.comparison || {};
            normalized.composition = normalized.composition || {};
            normalized.statistical_insights = normalized.statistical_insights || [];
        } else if (view === 'inventory') {
            normalized.low_stock = normalized.low_stock || [];
            normalized.reorder_suggestions = normalized.reorder_suggestions || [];
            normalized.top_sold = normalized.top_sold || [];
            normalized.low_stock_count = normalized.low_stock_count || 0;
            normalized.purchase_ready_count = normalized.reorder_suggestions.filter((item) => item.can_create_po).length;
            normalized.composition = normalized.composition || {};
            normalized.statistical_insights = normalized.statistical_insights || [];
        }
        return normalized;
    }

    async _loadData() {
        const view = this.state.view;
        console.log(`[KDSDashboard] Loading data for view: ${view}`);
        
        if (view === 'waste') {
            if (this.state.wasteTab === 'report') await this._loadWasteReport();
            return;
        }
        const routeMap = {
            manager:   '/mcd_kds/dashboard',
            kitchen:   '/mcd_kds/kitchen',
            expo:      '/mcd_kds/expo',
            sales:     '/mcd_kds/sales',
            inventory: '/mcd_kds/inventory',
        };
        const route = routeMap[view];
        if (!route) return;

        this.state.loading = true;
        this.state.error   = null;
        try {
            const effectiveRoute = route;
            console.log(`[KDSDashboard] Fetching from: ${effectiveRoute}`);
            const payload = this._periodPayload();
            console.log('[KDSDashboard] Sending payload:', JSON.stringify(payload));
            let data;
            try {
                data = await this.rpc(effectiveRoute, payload);
            } catch (filteredError) {
                if (payload.selected_date || payload.selected_month || payload.selected_year) {
                    throw filteredError;
                }
                console.warn('[KDSDashboard] Filtered dashboard request failed, retrying without date filters:', filteredError);
                data = await this.rpc(effectiveRoute, {
                    period: this.state.period,
                });
            }
            data = this._normalizeDashboardData(view, data);
            if (view === 'sales') {
                try {
                    const dailyPayload = this._periodPayload();
                    data.daily_products = await this.rpc('/mcd_kds/sales/daily_products', dailyPayload);
                } catch (dailyError) {
                    console.warn('[KDSDashboard] Cannot load daily product sales:', dailyError);
                    data.daily_products = [];
                }
            }
            this.state.data = this._normalizeDashboardData(view, data);
            console.log(`[KDSDashboard] Data received:`, this.state.data);
        } catch(e) {
            console.error('[KDSDashboard] Data load error:', e);
            this.state.error = 'Không tải được dữ liệu: ' + (e.message || e);
        } finally {
            this.state.loading = false;
            // Multiple retries to ensure chart renders with DOM ready
            setTimeout(() => this._syncCharts(), 50);
            setTimeout(() => this._syncCharts(), 150);
            setTimeout(() => this._syncCharts(), 300);
            setTimeout(() => this._syncCharts(), 800);
        }
    }

    async _loadWasteReport() {
        this.state.loading = true;
        try {
            const route = this.state.useDemo ? '/mcd_kds/demo/waste' : '/mcd_kds/waste/report';
            const payload = this._periodPayload();
            const report = await this.rpc(route, payload);
            this.state.wasteReport = {
                ...(report || {}),
                by_reason: report?.by_reason || [],
                by_product: report?.by_product || [],
                comparison: report?.comparison || {},
                statistical_insights: report?.statistical_insights || [],
            };
        } catch(e) {
            this.state.error = 'Không tải được báo cáo waste: ' + (e.message || e);
        } finally {
            this.state.loading = false;
        }
    }

    //  waste form 
    selectInventorySuggestion(item) {
        this.state.selectedReorderProductId = item?.product_id || null;
    }

    async openInventoryProduct(item, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        const productId = item?.product_id;
        if (!productId) {
            return;
        }
        await this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Nguyên liệu',
            res_model: 'product.product',
            res_id: productId,
            views: [[false, 'form']],
            target: 'current',
        });
    }

    async createPurchaseOrder(item, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        if (!item?.product_id || !item.can_create_po || this.state.purchaseCreatingProductId) {
            return;
        }
        this.state.purchaseCreatingProductId = item.product_id;
        try {
            const result = await this.rpc('/mcd_kds/inventory/create_purchase_order', {
                product_id: item.product_id,
                qty: item.suggest_qty,
            });
            if (!result?.success) {
                this.notification.add(result?.error || 'Không tạo được đơn mua hàng.', { type: 'warning' });
                return;
            }
            this.notification.add(`Da to ${result.purchase_order_name}`, { type: 'success' });
            if (result.action) {
                await this.action.doAction(result.action);
            }
        } catch (e) {
            console.error('[Inventory] Cannot create purchase order:', e);
            this.notification.add('Không tạo được đơn mua hàng: ' + (e.message || e), { type: 'danger' });
        } finally {
            this.state.purchaseCreatingProductId = null;
        }
    }

    addEntry() {
        this.state.wasteEntries = [...this.state.wasteEntries, EMPTY_ENTRY()];
    }

    removeEntry(idx) {
        const entries = [...this.state.wasteEntries];
        if (entries.length === 1) { this.state.wasteEntries = [EMPTY_ENTRY()]; return; }
        entries.splice(idx, 1);
        this.state.wasteEntries = entries;
    }

    updateEntry(idx, field, value) {
        const entries = [...this.state.wasteEntries];
        entries[idx] = { ...entries[idx], [field]: value };
        this.state.wasteEntries = entries;
    }

    // Product dropdown
    openDropdown(idx) {
        this.state.dropdownOpen = { ...this.state.dropdownOpen, [idx]: true };
    }

    closeDropdown(idx) {
        setTimeout(() => {
            const d = { ...this.state.dropdownOpen };
            d[idx] = false;
            this.state.dropdownOpen = d;
        }, 180);
    }

    selectProduct(idx, product) {
        const entries = [...this.state.wasteEntries];
        entries[idx] = {
            ...entries[idx],
            product_id:   product.id,
            product_name: product.name,
            search:       product.name,
        };
        this.state.wasteEntries   = entries;
        this.state.dropdownOpen   = { ...this.state.dropdownOpen, [idx]: false };
    }

    onSearchInput(idx, value) {
        const entries = [...this.state.wasteEntries];
        entries[idx] = { ...entries[idx], search: value, product_id: null, product_name: value };
        this.state.wasteEntries = entries;
        this.state.dropdownOpen = { ...this.state.dropdownOpen, [idx]: true };
    }

    async submitWaste() {
        const valid = this.state.wasteEntries.filter(e => e.product_name && e.product_name.trim());
        if (!valid.length) {
            this.state.wasteError = 'Vui lòng chọn ít nhất 1 nguyên liệu.';
            return;
        }
        this.state.submitting = true;
        this.state.wasteError = null;
        try {
            const res = await this.rpc('/mcd_kds/waste/submit', {
                entries: valid,
                waste_date: this.state.wasteDate || this.todayDate(),
            });
            if (res.success) {
                this.state.wasteSuccess = res.created.length;
                this.state.wasteEntries = [EMPTY_ENTRY()];
            } else {
                this.state.wasteError = res.error || 'Không lưu được hàng huỷ.';
            }
        } catch(e) {
            this.state.wasteError = 'Lưu thất bại: ' + (e.message || e);
        } finally {
            this.state.submitting = false;
        }
    }

    //  charts 
    _syncCharts() {
        const d    = this.state.data;
        const view = this.state.view;

        if (!window.Chart) {
            _loadChartJS().then((loaded) => {
                if (loaded) this._syncCharts();
            });
            return;
        }

        const chartToken = JSON.stringify({
            view,
            wasteTab: this.state.wasteTab,
            revenue: d?.chart_revenue,
            monthly: d?.monthly_chart,
            product: d?.product_chart,
            salesMetric: this.state.salesMetric,
            salesChartType: this.state.salesChartType,
            salesSort: this.state.salesSort,
            waste: this.state.wasteReport?.by_reason,
            inventoryTopSold: d?.top_sold,
            selectedProduct: this.state.selectedProduct?.product_id,
            selectedProductTrend: this.state.selectedProduct?.sales_trend,
            selectedProductHourly: this.state.selectedProduct?.sales_by_hour,
        });
        const expectedCharts = [];
        if (view === 'manager' && d?.chart_revenue) expectedCharts.push('revChart');
        if (view === 'sales' && !this.state.selectedProduct && d?.monthly_chart?.labels?.length) {
            expectedCharts.push('salesMonthlyChart');
        }
        if (view === 'sales' && !this.state.selectedProduct && (d?.top_products || []).length) {
            expectedCharts.push('salesProductChart');
        }
        if (view === 'waste' && this.state.wasteTab === 'report' && this.state.wasteReport?.by_reason?.length) {
            expectedCharts.push('wasteReasonChart');
        }
        if (view === 'inventory' && (d?.top_sold || []).length) {
            expectedCharts.push('inventoryIngredientChart');
        }
        if (view === 'sales' && this.state.selectedProduct?.sales_trend?.labels?.length) {
            expectedCharts.push('productTrendChart');
        }
        if (view === 'sales' && this.state.selectedProduct?.sales_by_hour?.labels?.length) {
            expectedCharts.push('productHourlyChart');
        }
        const chartRefs = {
            revChart: this.revChart,
            wasteReasonChart: this.wasteReasonChart,
            salesMonthlyChart: this.salesMonthlyChart,
            salesProductChart: this.salesProductChart,
            productTrendChart: this.productTrendChart,
            productHourlyChart: this.productHourlyChart,
            inventoryIngredientChart: this.inventoryIngredientChart,
        };
        if (
            this._chartToken === chartToken &&
            expectedCharts.length &&
            expectedCharts.every((key) => this._charts[key]?.canvas === chartRefs[key]?.el)
        ) {
            return;
        }
        this._chartToken = chartToken;

        if (view === 'manager' && d?.chart_revenue)
            this._buildLineChart('revChart', this.revChart,
                d.chart_revenue.labels, d.chart_revenue.values, 'Doanh thu (triệu)', '#DA291C');

        if (view === 'waste' && this.state.wasteTab === 'report' && this.state.wasteReport?.by_reason?.length)
            this._buildDoughnutChart('wasteReasonChart', this.wasteReasonChart,
                this.state.wasteReport.by_reason.map(r => r.reason),
                this.state.wasteReport.by_reason.map(r => r.qty));

        if (view === 'sales' && !this.state.selectedProduct && d?.monthly_chart?.labels?.length) {
            const monthlyChart = this.salesMonthlyChartData();
            if (this.state.salesChartType === 'line') {
                this._buildLineChart('salesMonthlyChart', this.salesMonthlyChart,
                    monthlyChart.labels, monthlyChart.values, monthlyChart.label, monthlyChart.color);
            } else if (this.state.salesChartType === 'pie') {
                this._buildDoughnutChart('salesMonthlyChart', this.salesMonthlyChart,
                    monthlyChart.labels, monthlyChart.values, monthlyChart.label);
            } else {
                this._buildColumnChart('salesMonthlyChart', this.salesMonthlyChart,
                    monthlyChart.labels, monthlyChart.values, monthlyChart.label, monthlyChart.color);
            }
        }

        if (view === 'sales' && !this.state.selectedProduct && (d?.top_products || []).length) {
            const productChart = this.salesProductChartData();
            if (this.state.salesChartType === 'line') {
                this._buildLineChart('salesProductChart', this.salesProductChart,
                    productChart.labels, productChart.values, productChart.label, productChart.color);
            } else if (this.state.salesChartType === 'pie') {
                this._buildDoughnutChart('salesProductChart', this.salesProductChart,
                    productChart.labels, productChart.values, productChart.label);
            } else {
                this._buildColumnChart('salesProductChart', this.salesProductChart,
                    productChart.labels, productChart.values, productChart.label, productChart.color);
            }
        }

        if (view === 'inventory' && (d?.top_sold || []).length)
            this._buildBarChart('inventoryIngredientChart', this.inventoryIngredientChart,
                d.top_sold.map((item) => item.name),
                d.top_sold.map((item) => item.chart_value ?? item.usage_value ?? item.qty),
                'Tỷ trọng giá trị tiêu hao', '#27AE60');

        // Product trend chart for sales view
        if (view === 'sales' && this.state.selectedProduct?.sales_trend?.labels?.length)
            this._buildLineChart('productTrendChart', this.productTrendChart,
                this.state.selectedProduct.sales_trend.labels,
                this.state.selectedProduct.sales_trend.values,
                'Số lượng bán', '#DA291C');
        if (view === 'sales' && this.state.selectedProduct?.sales_by_hour?.labels?.length)
            this._buildLineChart('productHourlyChart', this.productHourlyChart,
                this.state.selectedProduct.sales_by_hour.labels,
                this.state.selectedProduct.sales_by_hour.values,
                'Số lượng bán', '#27AE60');
    }

    _destroyCharts() {
        Object.values(this._charts).forEach(c => { try { c.destroy(); } catch(_) {} });
        this._charts = {};
        this._chartToken = null;
    }

    _formatChartValue(key, value) {
        if (key === 'revChart') {
            return `${value} tr VND`;
        }
        if (key === 'salesMonthlyChart') {
            const metric = this.state.salesMetric || 'revenue';
            if (metric === 'revenue') {
                return `${value} tr VND`;
            }
            return `${value} ${this.salesMetricUnit(metric)}`;
        }
        if (key === 'salesProductChart') {
            return `${value} ${this.salesMetricUnit()}`;
        }
        if (key === 'productTrendChart' || key === 'productHourlyChart') {
            return `${value} món`;
        }
        if (key === 'inventoryIngredientChart') {
            return `${value || 0}%`;
        }
        if (key === 'wasteReasonChart') {
            return `${value} sp`;
        }
        return String(value);
    }

    _chartTitle(key, datasetLabel) {
        const selectedName = this.state.selectedProduct?.product_name || '';
        const titles = {
            inventoryIngredientChart: 'Tỷ trọng tiêu hao nguyên liệu',
            revChart: 'Doanh thu tổng quan',
            salesMonthlyChart: this.state.salesMetric === 'revenue' ? 'Doanh thu theo thời gian' : `${this.salesMetricLabel()} theo thời gian`,
            salesProductChart: `Sản phẩm nổi bật theo ${this.salesMetricLabel().toLowerCase()}`,
            productTrendChart: selectedName ? `${selectedName} - 7 ngày gần nhất` : 'Xu hướng bán',
            productHourlyChart: selectedName ? `${selectedName} - bán theo giờ` : 'Bán theo giờ',
            wasteReasonChart: 'Hàng huỷ theo lý do',
        };
        return titles[key] || datasetLabel || 'Thong tin bieu do';
    }

    _chartPointDetail(key, label, index, value) {
        if (key === 'revChart' || key === 'salesMonthlyChart') {
            const details = key === 'revChart'
                ? this.state.data?.chart_revenue?.details
                : this.state.data?.monthly_chart?.details;
            const detail = Array.isArray(details)
                ? details[index]
                : null;
            if (detail) {
                return {
                    details: [
                        { label: 'Doanh thu', value: this.formatVND(detail.revenue || 0) },
                        { label: 'Số món', value: `${this.formatQty(detail.qty || 0)} món` },
                        { label: 'Số đơn', value: `${detail.orders || 0} đơn` },
                        { label: 'Giá trị đơn TB', value: this.formatVND(detail.avg_order_value || 0) },
                    ],
                    itemsTitle: 'Món nổi bật tại điểm này',
                    items: (detail.top_products || []).map((item) => ({
                        name: item.name,
                        meta: `${this.formatQty(item.qty || 0)} món`,
                        value: this.formatVND(item.revenue || 0),
                    })),
                    hint: key === 'salesMonthlyChart'
                        ? 'Dùng điểm này để biết mốc nào bán tốt và món nào đóng góp nhiều.'
                        : 'Dùng điểm này để xác định thời điểm cao điểm trên tổng quan.',
                };
            }
        }

        if (key === 'wasteReasonChart') {
            const reason = (this.state.wasteReport?.by_reason || []).find((item) => item.reason === label);
            return {
                details: [
                    { label: 'Số lượng huỷ', value: `${this.formatWasteQty(reason?.qty || value || 0)} đv` },
                    { label: 'Thất thoát', value: this.formatVND(reason?.loss || 0) },
                    { label: 'Lý do', value: label },
                ],
                itemsTitle: 'Nguyên liệu huỷ nhiều',
                items: (this.state.wasteReport?.by_product || []).slice(0, 5).map((item) => ({
                    name: item.name,
                    meta: `${this.formatWasteQty(item.qty || 0)} đv`,
                    value: this.formatVND(item.loss || 0),
                })),
                hint: 'Chuyển sang phần nhập hoặc báo cáo hàng huỷ để kiểm tra nguyên nhân và ghi nhận chi tiết.',
            };
        }

        if (key === 'productTrendChart' || key === 'productHourlyChart') {
            const overview = this.state.selectedProduct?.overview || {};
            return {
                details: [
                    { label: 'Sản phẩm', value: this.state.selectedProduct?.product_name || '' },
                    { label: key === 'productTrendChart' ? 'Ngày' : 'Giờ', value: label },
                    { label: 'Số lượng điểm chọn', value: `${this.formatQty(value || 0)} món` },
                    { label: 'Doanh thu sản phẩm', value: this.formatVND(overview.revenue || 0) },
                    { label: 'Số đơn có món', value: `${this.formatCount(overview.order_count || 0)} đơn` },
                ],
                itemsTitle: 'Món thường mua kèm',
                items: (this.state.selectedProduct?.often_bought_together || []).slice(0, 5).map((item) => ({
                    name: item.product,
                    meta: 'Mua kèm',
                    value: `${item.count || 0} x`,
                })),
                hint: 'Dùng điểm này để nhận ra ngày/giờ bán mạnh hoặc yếu của riêng sản phẩm.',
            };
        }

        return {
            details: [
                { label: 'Điểm dữ liệu', value: label },
                { label: 'Giá trị', value: this._formatChartValue(key, value) },
            ],
            items: [],
            hint: 'Bấm các điểm khác trên biểu đồ để so sánh.',
        };
    }

    _inventoryIngredientPointDetail(label, index, value) {
        const item = (this.state.data?.top_sold || [])[index] || {};
        const unit = item.uom || 'đơn vị';
        const usageValue = item.usage_value || 0;
        const usageShare = item.usage_share ?? value ?? 0;
        return {
            details: [
                { label: 'Nguyên liệu', value: item.name || label },
                { label: 'Tỷ trọng tiêu hao', value: `${usageShare}%` },
                { label: 'Số lượng tiêu thụ', value: this.formatMeasure(item.qty ?? 0, unit) },
                { label: 'Giá trị tiêu hao ước tính', value: this.formatVND(usageValue) },
                { label: 'Nguồn dữ liệu', value: 'BOM/công thức món đã bán' },
            ],
            itemsTitle: 'Nguyên liệu tiêu thụ nhiều',
            items: (this.state.data?.top_sold || []).slice(0, 5).map((row) => ({
                name: row.name,
                meta: `${row.usage_share || 0}% tiêu hao`,
                value: `${this.formatVND(row.usage_value || 0)} - ${this.formatMeasure(row.qty || 0, row.uom || 'đơn vị')}`,
            })),
            hint: 'Biểu đồ dùng tỷ trọng giá trị tiêu hao để thấy nguyên liệu nào đang chiếm phần lớn chi phí kho; số lượng thực tế vẫn giữ theo đơn vị kho.',
        };
    }

    _onChartPointClick(key, index, labels, values, datasetLabel) {
        const label = labels?.[index];
        const value = values?.[index];
        if (label === undefined || value === undefined) return;

        if (key === 'salesProductChart') {
            const product = this.salesProductOptions().find((p) => p.name === label);
            if (product) {
                this.selectProductFromSearch(product);
                return;
            }
        }

        const drilldown = key === 'inventoryIngredientChart'
            ? this._inventoryIngredientPointDetail(label, index, value)
            : this._chartPointDetail(key, label, index, value);
        this.state.chartSelection = {
            title: this._chartTitle(key, datasetLabel),
            label,
            value,
            displayValue: this._formatChartValue(key, value),
            details: drilldown.details || [],
            itemsTitle: drilldown.itemsTitle || '',
            items: drilldown.items || [],
            hint: drilldown.hint || '',
        };
    }

    _chartClickOptions(key, labels, values, datasetLabel) {
        return {
            onClick: (event, elements, chart) => {
                let active = elements || [];
                if ((!active.length) && chart?.getElementsAtEventForMode) {
                    active = chart.getElementsAtEventForMode(event, 'nearest', { intersect: true }, true);
                }
                const item = active && active[0];
                const index = item?.index ?? item?._index;
                if (index !== undefined) {
                    this._onChartPointClick(key, index, labels, values, datasetLabel);
                }
            },
        };
    }

    _buildLineChart(key, ref, labels, values, label, color) {
        if (!ref || !ref.el) {
            console.warn(`[Chart] ref.el not ready for ${key}`, ref);
            return;
        }
        if (!window.Chart) {
            console.warn('[Chart] Chart.js not loaded');
            return;
        }
        
        try {
            if (this._charts[key]) { this._charts[key].destroy(); delete this._charts[key]; }
            const canvas = ref.el;
            const isDarkChart = key === 'salesMonthlyChart' || key === 'salesProductChart';
            
            // Ensure canvas parent has proper dimensions
            if (canvas.parentElement) {
                const parent = canvas.parentElement;
                parent.style.position = 'relative';
                parent.style.width = '100%';
                parent.style.height = isDarkChart ? '360px' : '260px';
            }
            
            // Ensure canvas itself is ready
            canvas.style.maxWidth = '100%';
            canvas.style.cursor = 'pointer';
            
            this._charts[key] = new window.Chart(canvas, {
                type: 'line',
                data: { labels, datasets: [{ label, data: values,
                    borderColor: color, backgroundColor: color + '22',
                    fill: true, tension: 0.4, pointRadius: 4, pointHoverRadius: 6 }] },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    ...this._chartClickOptions(key, labels, values, label),
                    scales: {
                        x: {
                            grid: { color: isDarkChart ? '#343946' : '#f0f0f0' },
                            ticks: { color: isDarkChart ? '#d7dae3' : '#666' }
                        },
                        y: {
                            grid: { color: isDarkChart ? '#343946' : '#f0f0f0' },
                            ticks: { color: isDarkChart ? '#d7dae3' : '#666' },
                            beginAtZero: true
                        }
                    }
                }
            });
        } catch(e) {
            console.error(`[Chart] Error building line chart ${key}:`, e);
        }
    }

    _buildBarChart(key, ref, labels, values, label, color) {
        if (!ref || !ref.el) {
            console.warn(`[Chart] ref.el not ready for ${key}`, ref);
            return;
        }
        if (!window.Chart) {
            console.warn('[Chart] Chart.js not loaded');
            return;
        }
        
        try {
            if (this._charts[key]) { this._charts[key].destroy(); delete this._charts[key]; }
            const canvas = ref.el;
            
            // Ensure canvas parent has proper dimensions
            if (canvas.parentElement) {
                const parent = canvas.parentElement;
                parent.style.position = 'relative';
                parent.style.width = '100%';
                const n = labels.length || 6;
                parent.style.height = Math.max(250, n * 40) + 'px';
            }
            
            canvas.style.maxWidth = '100%';
            canvas.style.cursor = 'pointer';
            
            this._charts[key] = new window.Chart(canvas, {
                type: 'bar',
                data: { labels, datasets: [{ label, data: values,
                    backgroundColor: color + 'cc', borderColor: color,
                    borderWidth: 1, borderRadius: 4 }] },
                options: {
                    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: (ctx) => `${ctx.dataset.label}: ${this._formatChartValue(key, ctx.parsed?.x ?? ctx.raw)}`,
                            },
                        },
                    },
                    ...this._chartClickOptions(key, labels, values, label),
                    scales: {
                        x: {
                            grid: { color: '#f0f0f0' },
                            beginAtZero: true,
                            ticks: {
                                callback: (tickValue) => key === 'inventoryIngredientChart'
                                    ? `${tickValue}%`
                                    : tickValue,
                            },
                        },
                        y: { grid: { display: false }, ticks: { font: { size: 11 } } }
                    }
                }
            });
        } catch(e) {
            console.error(`[Chart] Error building bar chart ${key}:`, e);
        }
    }

    _buildColumnChart(key, ref, labels, values, label, color) {
        if (!ref || !ref.el) {
            console.warn(`[Chart] ref.el not ready for ${key}`, ref);
            return;
        }
        if (!window.Chart) {
            console.warn('[Chart] Chart.js not loaded');
            return;
        }

        try {
            if (this._charts[key]) { this._charts[key].destroy(); delete this._charts[key]; }
            const canvas = ref.el;
            if (canvas.parentElement) {
                const parent = canvas.parentElement;
                parent.style.position = 'relative';
                parent.style.width = '100%';
                parent.style.height = '360px';
            }
            canvas.style.maxWidth = '100%';
            canvas.style.cursor = 'pointer';

            this._charts[key] = new window.Chart(canvas, {
                type: 'bar',
                data: { labels, datasets: [{ label, data: values,
                    backgroundColor: color + 'cc', borderColor: color,
                    borderWidth: 1, borderRadius: 4 }] },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    ...this._chartClickOptions(key, labels, values, label),
                    scales: {
                        x: { grid: { display: false }, ticks: { color: '#d7dae3', maxRotation: 22, minRotation: 12 } },
                        y: { grid: { color: '#343946' }, ticks: { color: '#d7dae3' }, beginAtZero: true }
                    }
                }
            });
        } catch(e) {
            console.error(`[Chart] Error building column chart ${key}:`, e);
        }
    }

    _buildDoughnutChart(key, ref, labels, values, label = 'Số lượng') {
        if (!ref || !ref.el) {
            console.warn(`[Chart] ref.el not ready for ${key}`, ref);
            return;
        }
        if (!window.Chart) {
            console.warn('[Chart] Chart.js not loaded');
            return;
        }
        
        try {
            if (this._charts[key]) { this._charts[key].destroy(); delete this._charts[key]; }
            const canvas = ref.el;
            const isDarkChart = key === 'salesMonthlyChart' || key === 'salesProductChart';
            
            // Ensure canvas parent has proper dimensions
            if (canvas.parentElement) {
                const parent = canvas.parentElement;
                parent.style.position = 'relative';
                parent.style.width = '100%';
                parent.style.height = isDarkChart ? '360px' : '260px';
            }
            
            canvas.style.maxWidth = '100%';
            canvas.style.cursor = 'pointer';
            
            const COLORS = ['#4EA6E8','#00C2A8','#FFC72C','#8E44AD','#DA291C','#E67E22','#2ECC71','#9B59B6','#16A085','#F39C12','#3498DB','#E74C3C'];
            this._charts[key] = new window.Chart(canvas, {
                type: 'doughnut',
                data: { labels, datasets: [{ data: values,
                    backgroundColor: COLORS.slice(0, values.length),
                    borderWidth: 2, borderColor: '#fff' }] },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    ...this._chartClickOptions(key, labels, values, label),
                    plugins: { legend: { position: 'right',
                        labels: { color: isDarkChart ? '#d7dae3' : '#333', font: { size: 12 }, padding: 16 } } }
                }
            });
        } catch(e) {
            console.error(`[Chart] Error building doughnut chart ${key}:`, e);
        }
    }
}

registry.category("actions").add("mcd_kds.KDSDashboard", KDSDashboard);
