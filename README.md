# CDTN A45987 - Source Code

De tai: Xay dung he thong ERP quan ly nha hang thuc an nhanh

Sinh vien: Nguyen Thanh Tung
MSSV: A45987

## Noi dung source code

Thu muc nay gom cac module tuy chinh duoc phat trien tren nen tang Odoo 17:

- `mcd_backend_theme`: tuy chinh giao dien backend.
- `mcd_customer`: quan ly thong tin khach hang.
- `mcd_dashboard`: dashboard quan tri, bao cao ban hang, bep, giao mon, kho va hang huy.
- `mcd_expo_display`: man hinh dieu phoi/giao mon.
- `mcd_kiosk`: giao dien kiosk dat mon tu phuc vu.
- `mcd_kitchen_display`: man hinh bep.
- `mcd_pos_modifier`: tuy chinh mon an trong POS va xu ly dong bo don hang.
- `mcd_pos_order_type`: phan loai hinh thuc don hang an tai cho/mang di.

So do ERD va tai lieu thiet ke duoc trinh bay trong bao cao do an. Repository nay chi gom source code cac module custom.

## Cach trien khai

1. Cai dat Odoo 17 va PostgreSQL.
2. Sao chep cac module `mcd_*` vao thu muc addons cua Odoo.
3. Cau hinh `addons_path` trong file `odoo.conf`.
4. Khoi dong Odoo va cap nhat danh sach ung dung.
5. Cai dat/cap nhat cac module custom can thiet.

Lenh cap nhat module mau:

```bash
python odoo-bin -d Do_an -u mcd_dashboard,mcd_kiosk,mcd_kitchen_display,mcd_expo_display,mcd_pos_modifier,mcd_pos_order_type,mcd_customer,mcd_backend_theme -c odoo.conf
```

## Ghi chu

Source code chi bao gom cac module custom cua de tai, khong bao gom toan bo ma nguon Odoo goc de tranh dung luong file qua lon.
