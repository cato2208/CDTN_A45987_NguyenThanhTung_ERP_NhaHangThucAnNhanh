/** @odoo-module **/

import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { Component, onMounted, onWillUnmount } from "@odoo/owl";
import { NavBar } from "@web/webclient/navbar/navbar";

class McdHome extends Component {
    static template = "mcd_backend_theme.Home";

    setup() {
        this.menu = useService("menu");

        onMounted(() => document.body.classList.add("o_mcd_home_active"));
        onWillUnmount(() => document.body.classList.remove("o_mcd_home_active"));
    }

    get apps() {
        return this.menu.getApps();
    }

    normalizedName(app) {
        return (app.name || "")
            .toLowerCase()
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .replace(/\u0111/g, "d");
    }

    appIcon(app) {
        const name = this.normalizedName(app);
        if (name.includes("kiosk")) return "fa fa-desktop";
        if (name.includes("kitchen") || name.includes("bep")) return "fa fa-cutlery";
        if (name.includes("expo") || name.includes("giao món")) return "fa fa-check-square-o";
        if (name.includes("customer") || name.includes("khach")) return "fa fa-address-card-o";
        if (name.includes("pos") || name.includes("ban hang")) return "fa fa-shopping-bag";
        if (name.includes("ton kho") || name.includes("kho")) return "fa fa-archive";
        if (name.includes("dashboard") || name.includes("báo cáo")) return "fa fa-line-chart";
        if (name.includes("mua hang")) return "fa fa-truck";
        if (name.includes("san xuat")) return "fa fa-cogs";
        if (name.includes("hóa đơn")) return "fa fa-file-text-o";
        if (name.includes("nhan vien")) return "fa fa-id-badge";
        if (name.includes("ung dung")) return "fa fa-th-large";
        if (name.includes("cai dat")) return "fa fa-sliders";
        if (name.includes("thao luan")) return "fa fa-comments";
        if (name.includes("lien he")) return "fa fa-phone";
        if (name.includes("lien ket")) return "fa fa-link";
        return "fa fa-square";
    }

    appTone(app) {
        const name = this.normalizedName(app);
        if (name.includes("kiosk")) return "kiosk";
        if (name.includes("kitchen") || name.includes("bep")) return "kitchen";
        if (name.includes("expo") || name.includes("giao món")) return "expo";
        if (name.includes("customer") || name.includes("khach")) return "customer";
        if (name.includes("pos") || name.includes("ban hang")) return "sales";
        if (name.includes("ton kho") || name.includes("kho")) return "stock";
        if (name.includes("dashboard") || name.includes("báo cáo")) return "analytics";
        if (name.includes("mua hang")) return "purchase";
        if (name.includes("hóa đơn")) return "invoice";
        if (name.includes("nhan vien")) return "people";
        if (name.includes("cai dat")) return "settings";
        return "default";
    }

    appDescription(app) {
        const name = this.normalizedName(app);
        if (name.includes("dashboard") || name.includes("báo cáo")) return "Tổng hợp doanh thu và hiệu suất";
        if (name.includes("kitchen") || name.includes("bep")) return "Theo dõi đơn đang chế biến";
        if (name.includes("expo") || name.includes("giao món")) return "Kiểm tra món trước khi phục vụ";
        if (name.includes("pos") || name.includes("ban hang")) return "Nhận đơn và xử lý thanh toán";
        if (name.includes("kiosk")) return "Đơn tự phục vụ tại quầy";
        if (name.includes("ton kho") || name.includes("kho")) return "Nguyên liệu, tồn kho và nhập xuất";
        if (name.includes("mua hang")) return "Đơn mua và nhà cung cấp";
        if (name.includes("customer") || name.includes("khach")) return "Hồ sơ và lịch sử khách hàng";
        if (name.includes("hóa đơn")) return "Chứng từ và đối soát thanh toán";
        if (name.includes("nhan vien")) return "Ca làm và hồ sơ nhân sự";
        if (name.includes("cai dat")) return "Thiết lập và phân quyền";
        return "Mở phân hệ";
    }

    findQuickApp(keyword) {
        return this.apps.find((app) => this.appTone(app) === keyword || this.normalizedName(app).includes(keyword));
    }

    async openQuickApp(keyword) {
        const app = this.findQuickApp(keyword === "kds" ? "kitchen" : keyword);
        if (app) {
            await this.openApp(app);
        }
    }

    async openApp(app) {
        await this.menu.selectMenu(app);
    }
}

registry.category("actions").add("mcd_backend_theme.Home", McdHome);

patch(NavBar.prototype, {
    openMcdHome() {
        return this.actionService.doAction({
            type: "ir.actions.client",
            name: "Trang chủ",
            tag: "mcd_backend_theme.Home",
        }, { clearBreadcrumbs: true });
    },
});
